"""
compare_snipers.py

Side-by-side print of local vs cloud sniper state.json so you can see
where they're tracking together vs diverging.

Reads:
  - C:\\Users\\dylan\\polymarket\\files\\oracle-lag-sniper\\var\\logs\\state.json
  - ~/.ols-sniper-cloud/var/logs/state.json (synced via sync_remote_sniper.py)

Run before/after sync-cloud.bat to see fresh numbers.

    python scripts/compare_snipers.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


LOCAL_STATE = Path("C:/Users/dylan/polymarket/files/oracle-lag-sniper/var/logs/state.json")
CLOUD_STATE = Path.home() / ".ols-sniper-cloud" / "var" / "logs" / "state.json"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  warn: could not parse {path}: {e}")
        return None


def _row(label: str, local_val, cloud_val) -> str:
    lstr = "(none)" if local_val is None else str(local_val)
    cstr = "(none)" if cloud_val is None else str(cloud_val)
    delta = ""
    if isinstance(local_val, (int, float)) and isinstance(cloud_val, (int, float)):
        d = local_val - cloud_val
        if d != 0:
            sign = "+" if d > 0 else ""
            delta = f"  (local {sign}{d})"
    return f"  {label:<22s} {lstr:>15s}  {cstr:>15s}{delta}"


def _ago(ts: float | None) -> str:
    if not ts:
        return "(unknown)"
    delta = datetime.now().timestamp() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta/60)}m ago"
    return f"{int(delta/3600)}h ago"


def _file_age(path: Path) -> str:
    if not path.exists():
        return "(no file)"
    return _ago(path.stat().st_mtime)


def main() -> int:
    local = _load(LOCAL_STATE)
    cloud = _load(CLOUD_STATE)

    print()
    print("=" * 64)
    print("SNIPER STATE COMPARISON")
    print("=" * 64)
    print()
    print(f"  {'':<22s} {'LOCAL':>15s}  {'CLOUD':>15s}")
    print(f"  {'':<22s} {'(Round Rock)':>15s}  {'(us-east-1)':>15s}")
    print()

    if local is None and cloud is None:
        print("  Neither file exists.  Run sync-cloud.bat and ensure local sniper is running.")
        return 1

    fields = [
        "mode",
        "started_at",
        "kill_switch",
        "circuit_breaker",
        "circuit_breaker_reason",
        "daily_pnl",
        "daily_pnl_reset_date",
        "cumulative_pnl",
        "total_trades",
        "total_wins",
        "consecutive_missed_fills",
    ]

    for f in fields:
        l = (local or {}).get(f)
        c = (cloud or {}).get(f)
        # Pretty-print started_at
        if f == "started_at":
            l_str = _ago(l) if l else None
            c_str = _ago(c) if c else None
            print(_row(f, l_str, c_str))
        else:
            print(_row(f, l, c))

    print()
    print("  state.json mtime:")
    print(f"    LOCAL : {_file_age(LOCAL_STATE)}")
    print(f"    CLOUD : {_file_age(CLOUD_STATE)} (latency of last sync)")

    # WR comparison
    if local and cloud:
        ltt = int(local.get("total_trades") or 0)
        lwn = int(local.get("total_wins") or 0)
        ctt = int(cloud.get("total_trades") or 0)
        cwn = int(cloud.get("total_wins") or 0)
        lwr = (lwn / ltt * 100) if ltt else 0
        cwr = (cwn / ctt * 100) if ctt else 0
        print()
        print(f"  WR (wins/total_trades):")
        print(f"    LOCAL : {lwr:5.1f}%  (n={ltt})")
        print(f"    CLOUD : {cwr:5.1f}%  (n={ctt})")
        if ltt >= 30 and ctt >= 30:
            gap = lwr - cwr
            sign = "+" if gap > 0 else ""
            print(f"    gap   : {sign}{gap:.1f}pp  (negative = cloud's latency advantage paying off)")
        else:
            print(f"    gap   : (need n>=30 on both before this is meaningful)")

    # Markets count
    if local and cloud:
        lm = len((local.get("markets") or {}))
        cm = len((cloud.get("markets") or {}))
        print()
        print(f"  markets being tracked:")
        print(f"    LOCAL : {lm}")
        print(f"    CLOUD : {cm}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
