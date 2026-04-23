"""
scanner.py

Polymarket sum-arb scanner (library form).

Detects `best_ask_yes + best_ask_no < sum_threshold` on Up/Down events
(tag 102127) closing within a time horizon, and fires a callback with
an opportunity dict shaped for paper_bridge.try_paper_trade.

Key v3 fix preserved: uses /events?tag_id=102127, not /markets —
short-duration Up/Down markets don't appear in /markets discovery.

The opportunity dict passed to on_opportunity:

    {
        "slug": str,
        "market_id": str,
        "question": str,
        "yes_label": str,        # actual outcome label (e.g. "Up")
        "no_label": str,         # actual outcome label (e.g. "Down")
        "best_ask_yes": float,
        "best_ask_no": float,
        "best_ask_yes_size": float,
        "best_ask_no_size": float,
        "max_pairs": float,
        "notional_usd": float,
        "profit_per_pair": float,
        "end_time": float,       # unix seconds
        "time_to_close_sec": float,
        "ts": float,             # detection timestamp (unix seconds)
    }

Dedup: fires at most once per (market_id) per scanner process. A market
lives ~5 minutes; the point of the paper lab is to sample the first
opportunity window per market, not spray duplicates on every book tick.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Set, Tuple, Union

import httpx
import websockets


GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_WS   = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

UP_OR_DOWN_TAG_ID = 102127

DEFAULT_SUM_THRESHOLD       = 0.99
DEFAULT_MIN_DEPTH_USD       = 5.0
DEFAULT_MAX_HOURS_OUT       = 2.0
DEFAULT_REFRESH_EVENTS_SEC  = 60

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "sumarb.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("scanner")

OpportunityCallback = Callable[[dict], Union[None, Awaitable[None]]]


@dataclass
class TokenBook:
    token_id: str
    best_ask: Optional[float] = None
    best_ask_size: Optional[float] = None
    best_bid: Optional[float] = None
    last_update: float = 0.0


@dataclass
class Market:
    market_id: str
    question: str
    slug: str
    end_time: float
    yes_label: str
    no_label: str
    yes_token: TokenBook
    no_token: TokenBook
    fired: bool = False


def _parse_iso(iso_str: str) -> Optional[float]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _parse_json_field(raw, default):
    """Polymarket sends some fields as stringified JSON, others as lists."""
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return default
    return default


async def discover_updown_markets(
    client: httpx.AsyncClient,
    max_hours: float,
) -> Dict[str, Market]:
    """Pull active Up/Down events via tag filter, extract their markets."""
    out: Dict[str, Market] = {}
    now = time.time()

    for offset in range(0, 2000, 100):
        try:
            resp = await client.get(
                f"{GAMMA_API}/events",
                params={
                    "tag_id": UP_OR_DOWN_TAG_ID,
                    "closed": "false",
                    "active": "true",
                    "limit": 100,
                    "offset": offset,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception as e:
            log.warning(f"/events pagination error at offset {offset}: {e!r}")
            break

        if not events:
            break

        for ev in events:
            ev_end = _parse_iso(ev.get("endDate"))
            if ev_end is None or ev_end <= now:
                continue
            if ev_end - now > max_hours * 3600:
                continue

            for m in ev.get("markets", []):
                if not m.get("active") or m.get("closed"):
                    continue

                token_ids = _parse_json_field(m.get("clobTokenIds"), [])
                if len(token_ids) < 2:
                    continue

                outcomes = _parse_json_field(m.get("outcomes"), [])
                # Crypto Up/Down markets are always two-outcome; if labels
                # are missing fall back to generic Yes/No so the pipeline
                # still flows (engine.buy is case-insensitive).
                yes_label = str(outcomes[0]) if len(outcomes) >= 1 else "Yes"
                no_label  = str(outcomes[1]) if len(outcomes) >= 2 else "No"

                market_end = _parse_iso(m.get("endDate")) or ev_end

                market = Market(
                    market_id=str(m["id"]),
                    question=m.get("question", ev.get("title", "")),
                    slug=(m.get("slug") or "").lower(),
                    end_time=market_end,
                    yes_label=yes_label,
                    no_label=no_label,
                    yes_token=TokenBook(token_id=str(token_ids[0])),
                    no_token=TokenBook(token_id=str(token_ids[1])),
                )
                out[market.market_id] = market

        if len(events) < 100:
            break

    return out


def _apply_book_snapshot(book: TokenBook, asks: list, bids: list) -> None:
    if asks:
        best = min(asks, key=lambda x: float(x["price"]))
        book.best_ask = float(best["price"])
        book.best_ask_size = float(best["size"])
    else:
        book.best_ask = None
        book.best_ask_size = None

    if bids:
        best = max(bids, key=lambda x: float(x["price"]))
        book.best_bid = float(best["price"])
    else:
        book.best_bid = None

    book.last_update = time.time()


def _handle_ws_message(
    msg: dict,
    token_map: Dict[str, Tuple[Market, str]],
) -> Optional[Market]:
    asset_id = str(msg.get("asset_id", ""))
    if asset_id not in token_map:
        return None

    market, side = token_map[asset_id]
    book = market.yes_token if side == "yes" else market.no_token
    event = msg.get("event_type")

    if event == "book":
        _apply_book_snapshot(book, msg.get("asks", []), msg.get("bids", []))
        return market

    if event == "price_change":
        # Incremental updates: we only detect on full book snapshots.
        # Stamp timestamp so the detector knows data is live.
        book.last_update = time.time()
        return market

    return None


def _build_opportunity(
    market: Market,
    sum_threshold: float,
    min_depth_usd: float,
) -> Optional[dict]:
    yes = market.yes_token
    no  = market.no_token

    if yes.best_ask is None or no.best_ask is None:
        return None

    total = yes.best_ask + no.best_ask
    if total >= sum_threshold:
        return None

    size_yes = yes.best_ask_size or 0.0
    size_no  = no.best_ask_size or 0.0
    max_pairs = min(size_yes, size_no)
    notional = max_pairs * total
    if notional < min_depth_usd:
        return None

    return {
        "slug":               market.slug,
        "market_id":          market.market_id,
        "question":           market.question,
        "yes_label":          market.yes_label,
        "no_label":           market.no_label,
        "best_ask_yes":       round(yes.best_ask, 4),
        "best_ask_no":        round(no.best_ask, 4),
        "best_ask_yes_size":  round(size_yes, 2),
        "best_ask_no_size":   round(size_no, 2),
        "max_pairs":          round(max_pairs, 2),
        "notional_usd":       round(notional, 2),
        "profit_per_pair":    round(1.0 - total, 4),
        "end_time":           market.end_time,
        "time_to_close_sec":  round(market.end_time - time.time(), 1),
        "ts":                 time.time(),
    }


def _log_alert_line(opp: dict) -> None:
    total = opp["best_ask_yes"] + opp["best_ask_no"]
    log.info(
        f"sum={total:.4f} "
        f"| +${opp['profit_per_pair']:.3f}/pair "
        f"| depth=${opp['notional_usd']:.2f} "
        f"| t-{opp['time_to_close_sec']:.0f}s "
        f"| {opp['question'][:55]}"
    )
    with LOG_FILE.open("a") as f:
        f.write(json.dumps({"iso": datetime.now(timezone.utc).isoformat(), **opp}) + "\n")


async def _stream_once(
    markets: Dict[str, Market],
    fired_ids: Set[str],
    on_opportunity: OpportunityCallback,
    sum_threshold: float,
    min_depth_usd: float,
    stop_after_sec: float,
) -> None:
    token_map: Dict[str, Tuple[Market, str]] = {}
    for m in markets.values():
        token_map[m.yes_token.token_id] = (m, "yes")
        token_map[m.no_token.token_id]  = (m, "no")

    if not token_map:
        await asyncio.sleep(stop_after_sec)
        return

    async with websockets.connect(CLOB_WS, ping_interval=20) as ws:
        subscription = {"type": "market", "assets_ids": list(token_map.keys())}
        await ws.send(json.dumps(subscription))

        deadline = time.time() + stop_after_sec
        while time.time() < deadline:
            try:
                remaining = max(0.5, deadline - time.time())
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            try:
                payload = json.loads(raw)
            except Exception:
                continue

            msgs = payload if isinstance(payload, list) else [payload]
            for msg in msgs:
                try:
                    market = _handle_ws_message(msg, token_map)
                    if market is None or market.fired or market.market_id in fired_ids:
                        continue

                    opp = _build_opportunity(market, sum_threshold, min_depth_usd)
                    if opp is None:
                        continue

                    market.fired = True
                    fired_ids.add(market.market_id)
                    _log_alert_line(opp)

                    result = on_opportunity(opp)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    # One bad packet or one failing callback must not kill
                    # the WS session. Log and keep listening.
                    log.exception(f"per-message handler error: {e!r}")


async def run_scanner(
    on_opportunity: OpportunityCallback,
    sum_threshold: float = DEFAULT_SUM_THRESHOLD,
    min_depth_usd: float = DEFAULT_MIN_DEPTH_USD,
    max_hours_out: float = DEFAULT_MAX_HOURS_OUT,
    refresh_events_sec: int = DEFAULT_REFRESH_EVENTS_SEC,
) -> None:
    """Long-running scanner. Rediscovers markets every refresh_events_sec,
    subscribes to their CLOB books, fires on_opportunity on each fresh
    detection (once per market_id for this process).
    """
    log.info(
        f"scanner starting: threshold={sum_threshold} "
        f"depth>=${min_depth_usd} horizon={max_hours_out}h"
    )

    fired_ids: Set[str] = set()

    async with httpx.AsyncClient() as client:
        while True:
            try:
                markets = await discover_updown_markets(client, max_hours_out)
                ts = datetime.now().strftime("%H:%M:%S")
                log.info(f"[{ts}] tracking {len(markets)} Up/Down markets "
                         f"(fired so far: {len(fired_ids)})")

                if not markets:
                    await asyncio.sleep(15)
                    continue

                await _stream_once(
                    markets,
                    fired_ids,
                    on_opportunity,
                    sum_threshold,
                    min_depth_usd,
                    refresh_events_sec,
                )

                # Evict fired_ids for markets that have rolled off — bounded memory.
                still_active = set(markets.keys())
                fired_ids &= still_active

            except asyncio.CancelledError:
                log.info("scanner cancelled")
                raise
            except Exception as e:
                log.error(f"scanner loop error: {e!r}, retrying in 5s")
                await asyncio.sleep(5)


# Allow running the scanner standalone for smoke-testing, without the bridge.
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )

    def _print_opportunity(opp: dict) -> None:
        print(
            f"OPP {opp['slug']:40s}  sum={opp['best_ask_yes']+opp['best_ask_no']:.4f}  "
            f"depth=${opp['notional_usd']:.2f}  t-{opp['time_to_close_sec']:.0f}s"
        )

    try:
        asyncio.run(run_scanner(on_opportunity=_print_opportunity))
    except KeyboardInterrupt:
        pass
