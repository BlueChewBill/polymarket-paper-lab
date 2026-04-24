"""
paper_bridge.py

Bridges a sum-arb scanner with agent-next/polymarket-paper-trader.

When the scanner detects best_ask_yes + best_ask_no < threshold on a
5-min Up/Down market, this fires paired FOK buys through the paper
Engine. The Engine walks the REAL Polymarket order book level by level
and reports actual fills plus slippage vs. top-of-book.

Per-trade record captures: claimed top-of-book, per-leg fills (avg
price, shares, fee, cost), combined actual sum, slippage in bps, and
rejection / error status.

Resolution P&L is intentionally NOT back-filled into trades.jsonl.
The file is append-only; settlement data is queried from the engine
at analysis time in notebooks/slippage_analysis.ipynb.
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv
from pm_trader.engine import Engine
from pm_trader.models import OrderRejectedError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner import run_scanner  # noqa: E402

load_dotenv()

#
# THRESHOLD RATIONALE (derived from first run, 2026-04-23)
#
#   Pre-fee break-even:  sum * 1.072 = 1.00  ->  sum = 0.9329
#   Observed slippage:   median +500 bps, p75 +943 bps, p95 +3376 bps
#
# For a filter that's profitable on the MEDIAN slippage case with ~2%
# expected edge:  top_of_book_sum + 0.05 < 0.913  ->  sum < 0.863.
# Rounded to 0.86. At 0.99 threshold, every single one of 45 filled
# trades was a post-fee loser (0% positive-edge in live data).
#
# Depth: 3x trade notional filters out top-of-book-only books.
#
SUM_THRESHOLD       = float(os.getenv("SUM_THRESHOLD", "0.86"))
MIN_DEPTH_USD       = float(os.getenv("MIN_DEPTH_USD", "15"))
MAX_HOURS_OUT       = float(os.getenv("MAX_HOURS_OUT", "2"))
TRADE_NOTIONAL_USD  = float(os.getenv("TRADE_NOTIONAL_USD", "5"))
ACCOUNT_NAME        = os.getenv("PM_ACCOUNT", "sumarb")
INITIAL_BALANCE_USD = float(os.getenv("INITIAL_BALANCE_USD", "500"))

REPO_ROOT   = Path(__file__).resolve().parent.parent
LOG_FILE    = REPO_ROOT / "logs" / "paper_bridge.log"
TRADES_FILE = REPO_ROOT / "data" / "trades.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("paper_bridge")


TRADE_SCHEMA_VERSION = 1


@dataclass
class Trade:
    schema_version: int
    ts: float
    market_slug: str
    market_id: str
    yes_label: str
    no_label: str
    claimed_ask_yes: float
    claimed_ask_no: float
    claimed_sum: float
    claimed_edge_pct: float
    yes_fill_avg: float = 0.0
    yes_fill_shares: float = 0.0
    yes_fill_fee: float = 0.0
    yes_fill_cost: float = 0.0
    no_fill_avg: float = 0.0
    no_fill_shares: float = 0.0
    no_fill_fee: float = 0.0
    no_fill_cost: float = 0.0
    actual_sum: float = 0.0
    slippage_bps: int = 0
    both_filled: bool = False
    rejected_leg: str = ""  # "", "yes", "no", "both"
    error: str = ""


def load_engine() -> Engine:
    """Construct Engine for ACCOUNT_NAME, initializing on first run.

    Replicates the `pm-trader --account <name> init --balance <x>` CLI
    path programmatically so the harness is a single command to start.
    """
    data_dir = Path.home() / ".pm-trader" / ACCOUNT_NAME
    first_run = not data_dir.exists()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = Engine(data_dir)
    if first_run:
        engine.init_account(balance=INITIAL_BALANCE_USD)
        log.info(f"initialized paper account {ACCOUNT_NAME} @ ${INITIAL_BALANCE_USD:.2f}")
    return engine


def _leg_fields(fill) -> tuple[float, float, float, float]:
    """Extract (avg_price, shares, fee, cost) from a TradeResult, safely."""
    t = getattr(fill, "trade", fill)
    return (
        float(getattr(t, "avg_price", 0.0) or 0.0),
        float(getattr(t, "shares", 0.0) or 0.0),
        float(getattr(t, "fee", 0.0) or 0.0),
        float(getattr(t, "amount_usd", 0.0) or 0.0),
    )


def try_paper_trade(engine: Engine, opp: dict) -> Trade:
    slug      = opp["slug"]
    ask_yes   = opp["best_ask_yes"]
    ask_no    = opp["best_ask_no"]
    yes_label = opp["yes_label"]
    no_label  = opp["no_label"]
    claimed   = ask_yes + ask_no

    trade = Trade(
        schema_version=TRADE_SCHEMA_VERSION,
        ts=time.time(),
        market_slug=slug,
        market_id=opp.get("market_id", ""),
        yes_label=yes_label,
        no_label=no_label,
        claimed_ask_yes=ask_yes,
        claimed_ask_no=ask_no,
        claimed_sum=claimed,
        claimed_edge_pct=(1.0 - claimed) * 100,
    )

    log.info(f"OPP {slug}  sum={claimed:.4f}  edge={trade.claimed_edge_pct:+.2f}%")

    yes_fill = None
    no_fill  = None

    try:
        yes_fill = engine.buy(slug, yes_label, TRADE_NOTIONAL_USD)
    except OrderRejectedError as e:
        trade.rejected_leg = "yes"
        trade.error = f"yes FOK rejected: {e}"
        log.warning(f"  REJECT yes  {e}")
    except Exception as e:
        trade.error = f"yes buy error: {e!r}"
        log.error(f"  ERR yes  {e!r}")

    try:
        no_fill = engine.buy(slug, no_label, TRADE_NOTIONAL_USD)
    except OrderRejectedError as e:
        trade.rejected_leg = "both" if trade.rejected_leg == "yes" else "no"
        trade.error = (trade.error + "; " if trade.error else "") + f"no FOK rejected: {e}"
        log.warning(f"  REJECT no  {e}")
    except Exception as e:
        trade.error = (trade.error + "; " if trade.error else "") + f"no buy error: {e!r}"
        log.error(f"  ERR no  {e!r}")

    if yes_fill is not None:
        (trade.yes_fill_avg, trade.yes_fill_shares,
         trade.yes_fill_fee, trade.yes_fill_cost) = _leg_fields(yes_fill)
    if no_fill is not None:
        (trade.no_fill_avg, trade.no_fill_shares,
         trade.no_fill_fee, trade.no_fill_cost) = _leg_fields(no_fill)

    trade.both_filled = (trade.yes_fill_shares > 0 and trade.no_fill_shares > 0)

    if trade.both_filled:
        trade.actual_sum   = trade.yes_fill_avg + trade.no_fill_avg
        trade.slippage_bps = int((trade.actual_sum - claimed) * 10000)
        log.info(
            f"  FILLED  actual_sum={trade.actual_sum:.4f}  "
            f"slip={trade.slippage_bps:+d}bps  "
            f"real_edge={(1 - trade.actual_sum) * 100:+.2f}%"
        )

    return trade


def persist(trade: Trade) -> None:
    with TRADES_FILE.open("a") as f:
        f.write(json.dumps(asdict(trade)) + "\n")


async def _resolve_per_market(engine: Engine) -> None:
    """Fallback: try engine.resolve(slug) for each open position
    individually, tolerating per-market failures. Silent on individual
    errors; aggregate summary at info level."""
    try:
        positions = engine.portfolio()
    except Exception as e:
        log.debug(f"portfolio read failed: {e!r}")
        return
    if not positions:
        return
    ok, skipped = 0, 0
    for pos in positions:
        slug = (pos.get("slug") if isinstance(pos, dict)
                else getattr(pos, "slug", None))
        if not slug:
            continue
        try:
            engine.resolve(slug)
            ok += 1
        except Exception:
            # Per-market failure (most often: slug has rolled off
            # gamma's cache). Expected and non-fatal.
            skipped += 1
    if ok or skipped:
        log.info(f"RESOLVE  per-market ok={ok} skipped={skipped}")


async def resolve_loop(engine: Engine, interval: int = 60):
    """Periodically settle any markets that have closed.

    pm_trader's resolve_all() is all-or-nothing: if one open position
    has a slug the gamma API no longer returns (short-duration markets
    roll off gamma's cache within hours), the whole bulk call raises.
    We fall back to per-market resolve that tolerates individual
    failures. Effect is cosmetic only -- phase-0 analysis reads from
    data/trades.jsonl, not pm_trader's DB.
    """
    while True:
        try:
            resolved = engine.resolve_all()
            if resolved:
                payout = sum(float(getattr(r, "payout", 0.0) or 0.0) for r in resolved)
                log.info(f"RESOLVE  settled={len(resolved)}  payout=${payout:.2f}")
        except Exception as e:
            log.debug(f"resolve_all (bulk) failed: {e!r}")
            await _resolve_per_market(engine)
        await asyncio.sleep(interval)


def print_session_summary(engine: Engine) -> None:
    if not TRADES_FILE.exists() or TRADES_FILE.stat().st_size == 0:
        log.info("session summary: no trades recorded.")
        return
    trades = [json.loads(l) for l in TRADES_FILE.read_text().splitlines() if l.strip()]
    n = len(trades)
    filled = sum(1 for t in trades if t.get("both_filled"))
    slips = [t["slippage_bps"] for t in trades if t.get("both_filled")]
    mean_slip = sum(slips) / len(slips) if slips else 0
    try:
        bal = engine.get_balance()
        cash = float(bal.get("cash", 0.0))
        total = float(bal.get("total_value", 0.0))
        pnl = float(bal.get("pnl", 0.0))
        log.info(
            f"session summary: opps={n}  filled={filled}  "
            f"fill_rate={(filled/n if n else 0):.1%}  mean_slip={mean_slip:+.0f}bps  "
            f"cash=${cash:.2f}  total=${total:.2f}  pnl=${pnl:+.2f}"
        )
    except Exception as e:
        log.warning(f"get_balance failed in summary: {e!r}")
        log.info(
            f"session summary: opps={n}  filled={filled}  "
            f"fill_rate={(filled/n if n else 0):.1%}  mean_slip={mean_slip:+.0f}bps"
        )


async def main():
    engine = load_engine()
    try:
        bal = engine.get_balance()
        log.info(
            f"paper account={ACCOUNT_NAME}  "
            f"cash=${float(bal.get('cash', 0.0)):.2f}  "
            f"total=${float(bal.get('total_value', 0.0)):.2f}"
        )
    except Exception as e:
        log.error(f"get_balance failed: {e!r}")

    resolver = asyncio.create_task(resolve_loop(engine))

    def on_opportunity(opp: dict) -> None:
        trade = try_paper_trade(engine, opp)
        persist(trade)

    try:
        await run_scanner(
            on_opportunity=on_opportunity,
            sum_threshold=SUM_THRESHOLD,
            min_depth_usd=MIN_DEPTH_USD,
            max_hours_out=MAX_HOURS_OUT,
        )
    finally:
        resolver.cancel()
        try:
            await resolver
        except asyncio.CancelledError:
            pass
        print_session_summary(engine)
        try:
            engine.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
