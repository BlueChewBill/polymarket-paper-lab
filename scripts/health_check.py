"""
health_check.py

Fast pre-run sanity check for paper_bridge.

Exercises the non-streaming, non-trading paths:
  1. Construct / auto-init the Engine for PM_ACCOUNT
  2. Read balance (catches get_balance dict-vs-dataclass issues)
  3. One /events?tag_id=102127 discovery call (catches endpoint / JSON drift)
  4. Print the first few discovered markets with their outcome labels

Run this before a long session to confirm the wiring is sound.
Exits 0 on success, 1 on any failure.

    python scripts/health_check.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
import httpx  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

ACCOUNT_NAME        = os.getenv("PM_ACCOUNT", "sumarb")
INITIAL_BALANCE_USD = float(os.getenv("INITIAL_BALANCE_USD", "500"))
MAX_HOURS_OUT       = float(os.getenv("MAX_HOURS_OUT", "2"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("health_check")


def check_engine() -> int:
    try:
        from pm_trader.engine import Engine  # noqa: WPS433
    except Exception as e:
        log.error(f"cannot import pm_trader.engine: {e!r}")
        log.error("  pip install -r requirements.txt ?")
        return 1

    try:
        data_dir = Path.home() / ".pm-trader" / ACCOUNT_NAME
        first_run = not data_dir.exists()
        data_dir.mkdir(parents=True, exist_ok=True)
        engine = Engine(data_dir)
        if first_run:
            engine.init_account(balance=INITIAL_BALANCE_USD)
            log.info(f"initialized account {ACCOUNT_NAME} @ ${INITIAL_BALANCE_USD:.2f}")
        bal = engine.get_balance()
        log.info(
            f"engine OK | account={ACCOUNT_NAME} | "
            f"cash=${float(bal.get('cash', 0.0)):.2f} | "
            f"total=${float(bal.get('total_value', 0.0)):.2f} | "
            f"pnl=${float(bal.get('pnl', 0.0)):+.2f}"
        )
        try:
            engine.close()
        except Exception:
            pass
        return 0
    except Exception as e:
        log.error(f"engine check failed: {e!r}")
        return 1


async def check_discovery() -> int:
    try:
        from scanner import discover_updown_markets  # noqa: WPS433
    except Exception as e:
        log.error(f"cannot import scanner: {e!r}")
        return 1

    try:
        async with httpx.AsyncClient() as client:
            markets = await discover_updown_markets(client, MAX_HOURS_OUT)
        log.info(f"discovery OK | {len(markets)} Up/Down markets within {MAX_HOURS_OUT}h")

        if not markets:
            log.warning("  (no markets in horizon — unusual; check network / tag_id)")
            return 0

        preview = sorted(markets.values(), key=lambda m: m.end_time)[:5]
        for m in preview:
            log.info(
                f"  {m.yes_label:>5s} / {m.no_label:<5s}  "
                f"closes +{int(m.end_time - __import__('time').time())}s  "
                f"{m.question[:55]}"
            )
        return 0
    except Exception as e:
        log.error(f"discovery check failed: {e!r}")
        return 1


def main() -> int:
    log.info("=== health_check ===")
    rc = 0
    rc |= check_engine()
    rc |= asyncio.run(check_discovery())
    log.info(f"=== {'PASS' if rc == 0 else 'FAIL'} ===")
    return rc


if __name__ == "__main__":
    sys.exit(main())
