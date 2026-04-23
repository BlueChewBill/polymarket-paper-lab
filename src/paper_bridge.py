"""
paper_bridge.py

Bridges a sum-arb scanner with agent-next/polymarket-paper-trader.

STATUS: Work in progress. Pending scanner refactor.

When the scanner detects best_ask_yes + best_ask_no < threshold on a
5-min Up/Down market, this fires paired buys through the paper-trader
Engine. The Engine walks the REAL Polymarket order book level by level
and reports actual fills including slippage.

Per-trade record captures: claimed top-of-book prices, actual fills,
slippage in bps, and final P&L at resolution.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from pm_trader.engine import Engine  # from polymarket-paper-trader

# Config (move to .env loading before running for real)
SUM_THRESHOLD      = 0.99
MIN_DEPTH_USD      = 5.0
MAX_HOURS_OUT      = 2
TRADE_NOTIONAL_USD = 5.0
ACCOUNT_NAME       = "sumarb"

LOG_FILE    = Path("logs/paper_bridge.log")
TRADES_FILE = Path("data/trades.jsonl")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("paper_bridge")


@dataclass
class Trade:
    ts: float
    market_slug: str
    claimed_ask_yes: float
    claimed_ask_no: float
    claimed_sum: float
    claimed_edge_pct: float
    yes_fill_avg: float = 0.0
    yes_fill_shares: float = 0.0
    no_fill_avg: float = 0.0
    no_fill_shares: float = 0.0
    actual_sum: float = 0.0
    slippage_bps: int = 0
    both_filled: bool = False
    resolved: bool = False
    winner: str = ""
    pnl_usd: float = 0.0


def try_paper_trade(engine: Engine, market: dict) -> Trade:
    slug    = market["slug"]
    ask_yes = market["best_ask_yes"]
    ask_no  = market["best_ask_no"]
    claimed = ask_yes + ask_no

    trade = Trade(
        ts=time.time(),
        market_slug=slug,
        claimed_ask_yes=ask_yes,
        claimed_ask_no=ask_no,
        claimed_sum=claimed,
        claimed_edge_pct=(1.0 - claimed) * 100,
    )

    log.info(f"OPP {slug}  sum={claimed:.4f}  edge={trade.claimed_edge_pct:+.2f}%")

    # NOTE: verify Engine.buy() return shape against installed pm_trader version
    try:
        yes_fill = engine.buy(slug, "yes", TRADE_NOTIONAL_USD)
        no_fill  = engine.buy(slug, "no",  TRADE_NOTIONAL_USD)
    except Exception as e:
        log.error(f"  fill error: {e}")
        return trade

    trade.yes_fill_avg    = getattr(yes_fill, "avg_price", 0.0)
    trade.yes_fill_shares = getattr(yes_fill, "shares",    0.0)
    trade.no_fill_avg     = getattr(no_fill,  "avg_price", 0.0)
    trade.no_fill_shares  = getattr(no_fill,  "shares",    0.0)
    trade.both_filled = (trade.yes_fill_shares > 0 and trade.no_fill_shares > 0)

    if trade.both_filled:
        min_shares = min(trade.yes_fill_shares, trade.no_fill_shares)
        actual_cost = (trade.yes_fill_avg * min_shares + trade.no_fill_avg * min_shares)
        trade.actual_sum   = actual_cost / min_shares
        trade.slippage_bps = int((trade.actual_sum - claimed) * 10000)
        log.info(
            f"  FILLED  actual_sum={trade.actual_sum:.4f}  "
            f"slip={trade.slippage_bps:+d}bps  "
            f"real_edge={(1 - trade.actual_sum) * 100:+.2f}%"
        )
    else:
        log.warning(f"  PARTIAL  yes={trade.yes_fill_shares:.1f} no={trade.no_fill_shares:.1f}")

    return trade


def persist(trade: Trade) -> None:
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRADES_FILE.open("a") as f:
        f.write(json.dumps(asdict(trade)) + "\n")


async def resolve_loop(engine: Engine, interval: int = 60):
    while True:
        try:
            result = engine.resolve_all()
            n = getattr(result, "resolved_count", 0)
            if n:
                log.info(f"RESOLVE  settled {n} markets")
        except Exception as e:
            log.error(f"resolve_all error: {e}")
        await asyncio.sleep(interval)


async def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    engine = Engine.load(account=ACCOUNT_NAME)
    bal = engine.get_balance()
    log.info(f"paper account={ACCOUNT_NAME}  cash=${bal.cash:.2f}")

    asyncio.create_task(resolve_loop(engine))

    # PENDING: import refactored scanner
    # from src.scanner import run_scanner
    #
    # def on_opportunity(market: dict):
    #     trade = try_paper_trade(engine, market)
    #     persist(trade)
    #
    # await run_scanner(
    #     on_opportunity=on_opportunity,
    #     sum_threshold=SUM_THRESHOLD,
    #     min_depth_usd=MIN_DEPTH_USD,
    #     max_hours_out=MAX_HOURS_OUT,
    # )
    raise NotImplementedError("scanner refactor pending — see issue #1")


if __name__ == "__main__":
    asyncio.run(main())
