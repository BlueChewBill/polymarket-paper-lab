"""
compare_strategies.py

Side-by-side report on two paper strategies running against live
Polymarket markets:

    1. polymarket-paper-lab sum-arb bridge    (data/trades.jsonl)
    2. oracle-lag-sniper demo mode            (~/.ols-sniper/sniper.db)

They answer different questions so the comparison isn't trade-for-trade.
What we want to know at end of a parallel run:

  - How often did each strategy fire?
  - When it did, how did it perform?
  - Which one produced more usable data per unit of time?
  - After fees and slippage, is either one actually profitable?

Stdlib-only. Run after a 5-7 day parallel session.

    # prerequisite: sync the sniper JSONL into SQLite first
    python scripts/sync_sniper_to_sqlite.py

    python scripts/compare_strategies.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAPER_JSONL = REPO_ROOT / "data" / "trades.jsonl"
DEFAULT_SNIPER_DB   = Path.home() / ".ols-sniper" / "sniper.db"


def _pct(n: int, d: int) -> str:
    return f"{(n / d * 100) if d else 0:.1f}%"


def _fmt_usd(x: float) -> str:
    return f"${x:+.2f}" if x else "$0.00"


def _load_paper_trades(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _load_sniper_trades(db: Path) -> list:
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM trade_outcomes ORDER BY logged_at"
        ).fetchall()
    except sqlite3.OperationalError:
        # fallback if view missing
        rows = conn.execute("SELECT * FROM trades ORDER BY logged_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _section_paper_lab(trades: list) -> Optional[dict]:
    if not trades:
        return None
    n = len(trades)
    filled = [t for t in trades if t.get("both_filled")]
    rejected = [t for t in trades if t.get("rejected_leg")]

    ts = [t["ts"] for t in trades if t.get("ts")]
    span_h = (max(ts) - min(ts)) / 3600 if len(ts) >= 2 else 0

    slips = [t["slippage_bps"] for t in filled]
    mean_slip = statistics.fmean(slips) if slips else 0

    fee_total = sum(t.get("yes_fill_fee", 0) + t.get("no_fill_fee", 0) for t in filled)
    cost_total = sum(t.get("yes_fill_cost", 0) + t.get("no_fill_cost", 0) for t in filled)

    # Post-fee edge per trade, averaged
    net_edges = []
    for t in filled:
        ys = t.get("yes_fill_shares", 0) or 0
        ns = t.get("no_fill_shares", 0) or 0
        if ys <= 0 or ns <= 0:
            continue
        min_sh = min(ys, ns)
        fee_per_pair = (t.get("yes_fill_fee", 0) + t.get("no_fill_fee", 0)) / min_sh
        net = (1.0 - t["actual_sum"] - fee_per_pair) * 100
        net_edges.append(net)
    mean_net_edge = statistics.fmean(net_edges) if net_edges else 0

    return {
        "n":             n,
        "filled":        len(filled),
        "fill_rate":     len(filled) / n if n else 0,
        "rejected":      len(rejected),
        "span_h":        span_h,
        "opps_per_hr":   n / span_h if span_h else 0,
        "mean_slip_bps": mean_slip,
        "fee_total":     fee_total,
        "cost_total":    cost_total,
        "mean_net_edge": mean_net_edge,
        "positive_net":  sum(1 for e in net_edges if e > 0),
        "net_samples":   len(net_edges),
    }


def _section_sniper(rows: list) -> Optional[dict]:
    if not rows:
        return None
    n = len(rows)

    resolved = [r for r in rows if r.get("resolved_pnl") is not None]
    wins     = [r for r in resolved if (r.get("resolved_pnl") or 0) > 0]

    pnls = [float(r["resolved_pnl"]) for r in resolved if r.get("resolved_pnl") is not None]
    total_pnl = sum(pnls)
    mean_pnl  = statistics.fmean(pnls) if pnls else 0

    fees = [float(r.get("fees", 0) or 0) for r in rows]
    fee_total = sum(fees)

    logged = [r["logged_at"] for r in rows if r.get("logged_at")]
    span_h = (max(logged) - min(logged)) / 3600 if len(logged) >= 2 else 0

    return {
        "n":              n,
        "resolved":       len(resolved),
        "wins":           len(wins),
        "win_rate":       len(wins) / len(resolved) if resolved else 0,
        "total_pnl":      total_pnl,
        "mean_pnl":       mean_pnl,
        "fee_total":      fee_total,
        "span_h":         span_h,
        "entries_per_hr": n / span_h if span_h else 0,
    }


def _render(paper: Optional[dict], sniper: Optional[dict]) -> None:
    print("=" * 60)
    print("STRATEGY COMPARISON")
    print("=" * 60)

    if paper is None:
        print("\npaper-lab sum-arb: no data at data/trades.jsonl")
    else:
        p = paper
        print(f"\npaper-lab sum-arb (polymarket-paper-lab)")
        print(f"  run span         : {p['span_h']:.1f}h  ({p['opps_per_hr']:.2f} opps/hr)")
        print(f"  opportunities    : {p['n']}")
        print(f"  both filled      : {p['filled']}  ({_pct(p['filled'], p['n'])})")
        print(f"  rejected         : {p['rejected']}  ({_pct(p['rejected'], p['n'])})")
        print(f"  mean slippage    : {p['mean_slip_bps']:+.0f} bps")
        if p['cost_total'] > 0:
            print(f"  fees paid        : ${p['fee_total']:.2f} on ${p['cost_total']:.2f} notional "
                  f"({p['fee_total']/p['cost_total']*100:.2f}%)")
        print(f"  mean post-fee edge: {p['mean_net_edge']:+.3f}%  "
              f"({p['positive_net']}/{p['net_samples']} positive)")

    if sniper is None:
        print("\noracle-lag-sniper: no data at ~/.ols-sniper/sniper.db")
        print("  run:  python scripts/sync_sniper_to_sqlite.py  first")
    else:
        s = sniper
        print(f"\noracle-lag-sniper (JonathanPetersonn, demo mode)")
        print(f"  run span         : {s['span_h']:.1f}h  ({s['entries_per_hr']:.2f} entries/hr)")
        print(f"  entries          : {s['n']}")
        print(f"  resolved         : {s['resolved']}")
        print(f"  wins             : {s['wins']}  ({_pct(s['wins'], s['resolved'])})")
        print(f"  total P&L        : {_fmt_usd(s['total_pnl'])}")
        print(f"  mean P&L / trade : {_fmt_usd(s['mean_pnl'])}")
        print(f"  fees paid        : ${s['fee_total']:.2f}")

    print()
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)
    lines = []

    if paper:
        if paper['n'] < 20:
            lines.append(f"paper-lab: too few opps ({paper['n']}) for a conclusion. Keep running.")
        elif paper['fill_rate'] < 0.25:
            lines.append(f"paper-lab: PHANTOM. {paper['fill_rate']:.0%} fill rate -- most detections vanish.")
        elif paper['mean_net_edge'] < 0:
            lines.append(f"paper-lab: FEE-TRAP. Fills happen ({paper['fill_rate']:.0%}) but "
                         f"mean post-fee edge is {paper['mean_net_edge']:+.2f}% -- losing to fees.")
        elif paper['positive_net'] / max(paper['net_samples'], 1) < 0.5:
            lines.append(f"paper-lab: MIXED. Some trades positive but majority lose after fees.")
        else:
            lines.append(f"paper-lab: LIVE EDGE. {paper['fill_rate']:.0%} fill, mean edge "
                         f"{paper['mean_net_edge']:+.2f}% post-fee.")

    if sniper:
        if sniper['resolved'] < 30:
            lines.append(f"sniper: too few resolved ({sniper['resolved']}) for a conclusion. Keep running.")
        elif sniper['win_rate'] < 0.55:
            lines.append(f"sniper: DEAD SIGNAL. WR={sniper['win_rate']:.1%} vs backtest 61.4%.")
        elif sniper['win_rate'] < 0.60:
            lines.append(f"sniper: MARGINAL. WR={sniper['win_rate']:.1%} close to backtest but not confirming.")
        elif sniper['total_pnl'] <= 0:
            lines.append(f"sniper: SIGNAL HOLDS but not profitable yet ({_fmt_usd(sniper['total_pnl'])}). "
                         f"WR={sniper['win_rate']:.1%}.")
        else:
            lines.append(f"sniper: LIVE EDGE. WR={sniper['win_rate']:.1%}, "
                         f"total P&L {_fmt_usd(sniper['total_pnl'])}.")

    if not lines:
        print("no data to verdict on. run both strategies first.")
    else:
        for l in lines:
            print(f"  {l}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper-jsonl", type=Path, default=DEFAULT_PAPER_JSONL)
    parser.add_argument("--sniper-db",   type=Path, default=DEFAULT_SNIPER_DB)
    args = parser.parse_args()

    paper = _section_paper_lab(_load_paper_trades(args.paper_jsonl))
    sniper = _section_sniper(_load_sniper_trades(args.sniper_db))
    _render(paper, sniper)
    return 0


if __name__ == "__main__":
    sys.exit(main())
