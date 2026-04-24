"""
dashboard.py

Tiny live dashboard for the paper lab. Reads:
  - ~/.pm-trader/sumarb/paper.db  (read-only, WAL-safe while bridge runs)
  - data/trades.jsonl             (our opportunity+fill log)

Refreshes every few seconds. Ctrl-C to quit. ASCII-only so it renders
clean on any Windows console without font/encoding surprises.

    python scripts/dashboard.py
    python scripts/dashboard.py --refresh 1
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


REPO_ROOT   = Path(__file__).resolve().parent.parent
TRADES_FILE = REPO_ROOT / "data" / "trades.jsonl"
PAPER_DB    = Path.home() / ".pm-trader" / "sumarb" / "paper.db"

WIDTH = 48  # inner content is WIDTH - 4 chars


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _rule() -> str:
    return "+" + "-" * (WIDTH - 2) + "+"


def _line(s: str) -> str:
    inner = WIDTH - 4
    # Truncate if too long; left-pad otherwise
    if len(s) > inner:
        s = s[:inner]
    return f"| {s:<{inner}} |"


def _read_balance() -> dict | None:
    if not PAPER_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM account LIMIT 1").fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _read_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    try:
        return [json.loads(l) for l in TRADES_FILE.read_text().splitlines() if l.strip()]
    except Exception:
        return []


def _summarize(trades: list[dict]) -> dict:
    n = len(trades)
    filled = sum(1 for t in trades if t.get("both_filled"))
    rejected = sum(1 for t in trades if t.get("rejected_leg"))
    errored = sum(1 for t in trades if t.get("error") and not t.get("rejected_leg"))
    slips = [t["slippage_bps"] for t in trades if t.get("both_filled")]
    mean_slip = sum(slips) / len(slips) if slips else 0

    fee_total = sum(t.get("yes_fill_fee", 0) + t.get("no_fill_fee", 0) for t in filled_trades(trades))

    # Latest trade (most recent fill/attempt)
    last_ts = max((t.get("ts", 0) for t in trades), default=0)

    return {
        "n":         n,
        "filled":    filled,
        "rejected":  rejected,
        "errored":   errored,
        "mean_slip": mean_slip,
        "fee_total": fee_total,
        "last_ts":   last_ts,
    }


def filled_trades(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t.get("both_filled")]


def _fmt_ago(ts: float) -> str:
    if not ts:
        return "never"
    delta = time.time() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    return f"{int(delta / 3600)}h ago"


def _render(bal: dict | None, stats: dict) -> str:
    out = [
        _rule(),
        _line("POLYMARKET PAPER LAB"),
        _line("account: sumarb"),
        _rule(),
    ]
    if bal is None:
        out.append(_line("(paper.db not found - run the bridge first)"))
    else:
        cash    = float(bal.get("cash", 0) or 0)
        pos_val = float(bal.get("positions_value", 0) or 0)
        total   = float(bal.get("total_value", 0) or 0)
        pnl     = float(bal.get("pnl", 0) or 0)
        out.extend([
            _line(f"cash           ${cash:>10.2f}"),
            _line(f"positions      ${pos_val:>10.2f}"),
            _line(f"total          ${total:>10.2f}"),
            _line(f"P&L            ${pnl:>+10.2f}"),
        ])
    out.append(_rule())

    n  = stats["n"]
    fl = stats["filled"]
    rj = stats["rejected"]
    er = stats["errored"]
    fr = (fl / n * 100) if n else 0
    ms = int(stats["mean_slip"])
    out.extend([
        _line(f"opportunities     {n:>5d}"),
        _line(f"both filled       {fl:>5d}  ({fr:5.1f}%)"),
        _line(f"rejected          {rj:>5d}"),
    ])
    if er:
        out.append(_line(f"errored           {er:>5d}"))
    out.append(_line(f"mean slippage   {ms:>+5d} bps"))
    if stats["fee_total"]:
        out.append(_line(f"fees paid       ${stats['fee_total']:>9.2f}"))
    out.append(_line(f"last activity   {_fmt_ago(stats['last_ts']):>12s}"))
    out.append(_rule())
    out.append(_line(f"updated {datetime.now().strftime('%H:%M:%S')}   Ctrl-C quit"))
    out.append(_rule())
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", type=float, default=3.0,
                        help="seconds between refreshes (default 3)")
    args = parser.parse_args()

    try:
        while True:
            _clear()
            bal = _read_balance()
            stats = _summarize(_read_trades())
            print(_render(bal, stats))
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
