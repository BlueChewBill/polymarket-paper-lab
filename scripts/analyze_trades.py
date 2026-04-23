"""
analyze_trades.py

Phase-0 go/no-go analysis for data/trades.jsonl.

Answers the core question -- "are detected sum-arb opportunities
actually fillable, or is this a phantom-liquidity chase?" -- by
bucketing the JSONL into:

  - Opportunity rate (count, per-hour)
  - Fill rate (both legs filled / total fired)
  - Rejection breakdown (which leg rejects most often)
  - Slippage distribution in bps (claimed sum vs actual sum)
  - Post-fee edge: claimed_edge - fees - slippage
  - Time-to-close at detection distribution

Stdlib-only so it runs without the full requirements.txt.

    python scripts/analyze_trades.py
    python scripts/analyze_trades.py --path data/trades.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import List


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "trades.jsonl"


def _pct(n: int, d: int) -> str:
    return f"{(n / d * 100) if d else 0:.1f}%"


def _quantiles(xs: List[float], labels=("min", "p25", "p50", "p75", "p95", "max")) -> str:
    if not xs:
        return "(no data)"
    xs_sorted = sorted(xs)
    def q(p: float) -> float:
        if p == 0:
            return xs_sorted[0]
        if p == 1:
            return xs_sorted[-1]
        i = p * (len(xs_sorted) - 1)
        lo, hi = int(i), min(int(i) + 1, len(xs_sorted) - 1)
        return xs_sorted[lo] + (xs_sorted[hi] - xs_sorted[lo]) * (i - lo)
    vals = {
        "min":  q(0),
        "p25":  q(0.25),
        "p50":  q(0.50),
        "p75":  q(0.75),
        "p95":  q(0.95),
        "max":  q(1),
    }
    return "  ".join(f"{k}={vals[k]:+.1f}" for k in labels)


def load_trades(path: Path) -> List[dict]:
    if not path.exists():
        print(f"no trades file at {path}")
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def analyze(trades: List[dict]) -> None:
    n = len(trades)
    if n == 0:
        print("no trades recorded.")
        return

    filled  = [t for t in trades if t.get("both_filled")]
    rejected = [t for t in trades if t.get("rejected_leg")]
    errored  = [t for t in trades if t.get("error") and not t.get("rejected_leg")]

    ts_values = [t["ts"] for t in trades if t.get("ts")]
    span_hours = (max(ts_values) - min(ts_values)) / 3600 if len(ts_values) >= 2 else 0

    print(f"=== trades.jsonl -- {n} opportunities ===")
    if span_hours > 0:
        print(f"span: {span_hours:.2f}h  ({n / span_hours:.2f} opps/hr)")
    print()
    print(f"outcomes:")
    print(f"  both filled : {len(filled):4d}  ({_pct(len(filled), n)})")
    print(f"  rejected    : {len(rejected):4d}  ({_pct(len(rejected), n)})")
    print(f"  errored     : {len(errored):4d}  ({_pct(len(errored), n)})")

    if rejected:
        legs = {"yes": 0, "no": 0, "both": 0}
        for t in rejected:
            legs[t["rejected_leg"]] = legs.get(t["rejected_leg"], 0) + 1
        print(f"\nrejection breakdown (which leg FOK'd):")
        for leg, count in legs.items():
            if count:
                print(f"  {leg:5s} : {count:4d}  ({_pct(count, len(rejected))})")

    if filled:
        print("\nclaimed edge (pre-fee, %) on FILLED trades:")
        claimed_edges = [t["claimed_edge_pct"] for t in filled]
        print(f"  mean={statistics.fmean(claimed_edges):+.2f}  "
              f"median={statistics.median(claimed_edges):+.2f}")
        print(f"  {_quantiles(claimed_edges)}")

        print("\nslippage (bps) -- actual_sum vs claimed_sum:")
        slips = [t["slippage_bps"] for t in filled]
        print(f"  mean={statistics.fmean(slips):+.1f}  "
              f"median={statistics.median(slips):+.1f}")
        print(f"  {_quantiles(slips)}")

        # Fee drag: fees paid / notional traded
        fees_total = sum(t.get("yes_fill_fee", 0) + t.get("no_fill_fee", 0) for t in filled)
        cost_total = sum(t.get("yes_fill_cost", 0) + t.get("no_fill_cost", 0) for t in filled)
        if cost_total > 0:
            print(f"\nfees: ${fees_total:.2f} paid on ${cost_total:.2f} notional "
                  f"({fees_total / cost_total * 100:.2f}%)")

        # Real edge: claimed_edge - fee_drag - slippage_drag, per trade
        # Approximation: net_edge_pct = (1 - actual_sum - fee_per_pair) * 100
        # fee_per_pair ≈ (yes_fee + no_fee) / min(yes_shares, no_shares)
        net_edges = []
        for t in filled:
            ys = t.get("yes_fill_shares", 0) or 0
            ns = t.get("no_fill_shares", 0) or 0
            min_shares = min(ys, ns) if (ys > 0 and ns > 0) else 0
            if min_shares == 0:
                continue
            fee_per_pair = (t.get("yes_fill_fee", 0) + t.get("no_fill_fee", 0)) / min_shares
            net = (1.0 - t["actual_sum"] - fee_per_pair) * 100
            net_edges.append(net)
        if net_edges:
            print(f"\npost-fee edge (%), per filled trade:")
            print(f"  mean={statistics.fmean(net_edges):+.3f}  "
                  f"median={statistics.median(net_edges):+.3f}")
            print(f"  {_quantiles(net_edges)}")
            positive = sum(1 for e in net_edges if e > 0)
            print(f"  positive-edge trades: {positive} / {len(net_edges)} "
                  f"({_pct(positive, len(net_edges))})")

    # Time-to-close distribution (need to reconstruct -- we don't persist it).
    # The ts at fire and end_time aren't both on the record today; skip unless
    # we extend the schema. Leaving this as a TODO reminder in the output.
    print()
    print("note: to see time-to-close at detection, extend Trade schema with end_time.")
    print()
    print("=== go/no-go heuristics ===")
    fill_rate = len(filled) / n if n else 0
    if fill_rate < 0.25:
        verdict = "LIKELY PHANTOM -- most detections don't fill at claimed prices."
    elif fill_rate < 0.50:
        verdict = "MIXED -- half the book is real, half vanishes. Dig into rejection timing."
    elif fill_rate < 0.80:
        verdict = "PROMISING -- most fill. Check post-fee edge distribution."
    else:
        verdict = "FILLABLE -- liquidity is real. Now check whether edge survives fees."
    print(f"  fill rate = {fill_rate:.1%}  ->  {verdict}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    trades = load_trades(args.path)
    analyze(trades)
    return 0


if __name__ == "__main__":
    sys.exit(main())
