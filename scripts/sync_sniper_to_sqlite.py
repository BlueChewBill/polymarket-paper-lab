"""
sync_sniper_to_sqlite.py

Reshape oracle-lag-sniper's JSONL output into a SQLite DB so it can be
queried alongside pm-trader's paper.db.

The sniper writes append-only JSONL to $OLS_HOME/var/logs/:
  - trades.jsonl       one row per (demo or live) trade entry
  - resolutions.jsonl  one row per market resolution (pnl + outcome)
  - signals.jsonl      every signal fire, whether or not it entered
  - attempts.jsonl     fill-attempt diagnostics
  - events.jsonl       startup/shutdown/circuit-breaker

We write to ~/.ols-sniper/sniper.db (WAL mode, matches pm-trader's
paper.db layout choice) with idempotent full rebuilds:

    CREATE TABLE trades      (order_id PRIMARY KEY, ...)
    CREATE TABLE resolutions ((market_ts, asset, side) PRIMARY KEY, ...)
    CREATE VIEW  trade_outcomes AS
        SELECT t.*, r.outcome AS resolved_outcome, r.pnl AS resolved_pnl
        FROM trades t LEFT JOIN resolutions r USING (market_ts, asset, side)

Full rebuild is fine: the JSONL is append-only and small. If it grows
past a few million rows we'll move to incremental sync.

Usage:
    python scripts/sync_sniper_to_sqlite.py
    python scripts/sync_sniper_to_sqlite.py --ols-home <dir> --db <path>
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_OLS_HOME = Path(os.environ.get(
    "OLS_HOME",
    "C:/Users/dylan/polymarket/files/oracle-lag-sniper",
))
DEFAULT_DB_PATH = Path.home() / ".ols-sniper" / "sniper.db"


TRADE_COLS = [
    ("order_id",                  "TEXT PRIMARY KEY"),
    ("logged_at",                 "REAL"),
    ("market_ts",                 "REAL"),
    ("asset",                     "TEXT"),
    ("entry_tick_ts",             "REAL"),
    ("side",                      "TEXT"),   # BUY_YES / BUY_NO
    ("entry_price",               "REAL"),
    ("raw_price",                 "REAL"),
    ("notional",                  "REAL"),
    ("fees",                      "REAL"),
    ("outcome",                   "TEXT"),   # may be populated at entry
    ("pnl",                       "REAL"),
    ("roi",                       "REAL"),
    ("time_remaining_at_entry",   "REAL"),
    ("delta_at_entry",            "REAL"),
    ("volume",                    "REAL"),
    ("mode",                      "TEXT"),   # demo / live
    ("fill_status",               "TEXT"),
    ("oracle_staleness_at_entry", "REAL"),
    ("oracle_ts_at_entry",        "REAL"),
]

RESOLUTION_COLS = [
    ("market_ts",   "REAL"),
    ("asset",       "TEXT"),
    ("side",        "TEXT"),
    ("outcome",     "TEXT"),     # Up / Down
    ("pnl",         "REAL"),
    ("entry_price", "REAL"),
    ("logged_at",   "REAL"),
]


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  warn: {path.name}:{lineno} malformed JSON: {e}",
                      file=sys.stderr)


def _pick(rec: dict, keys) -> tuple:
    """Extract a fixed tuple of fields from a record, None-filling missing."""
    return tuple(rec.get(k) for k in keys)


def _build_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW  IF EXISTS trade_outcomes")
    conn.execute("DROP TABLE IF EXISTS trades")
    conn.execute("DROP TABLE IF EXISTS resolutions")

    trade_ddl = ", ".join(f"{c[0]} {c[1]}" for c in TRADE_COLS)
    conn.execute(f"CREATE TABLE trades ({trade_ddl})")

    res_ddl = ", ".join(f"{c[0]} {c[1]}" for c in RESOLUTION_COLS)
    conn.execute(f"CREATE TABLE resolutions ({res_ddl}, "
                 f"PRIMARY KEY (market_ts, asset, side))")

    conn.execute("""
        CREATE VIEW trade_outcomes AS
        SELECT t.order_id, t.logged_at, t.market_ts, t.asset, t.side,
               t.entry_price, t.raw_price, t.notional, t.fees,
               t.delta_at_entry, t.time_remaining_at_entry, t.mode,
               COALESCE(r.outcome, t.outcome) AS resolved_outcome,
               COALESCE(r.pnl,     t.pnl)     AS resolved_pnl
        FROM trades t
        LEFT JOIN resolutions r
          ON r.market_ts = t.market_ts
         AND r.asset     = t.asset
         AND r.side      = t.side
    """)


def sync(ols_home: Path, db_path: Path) -> dict:
    logs_dir = ols_home / "var" / "logs"
    if not logs_dir.exists():
        print(f"  note: {logs_dir} does not exist yet "
              f"(sniper hasn't been run, or OLS_HOME is wrong)")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    _build_schema(conn)

    trade_key_names = [c[0] for c in TRADE_COLS]
    res_key_names   = [c[0] for c in RESOLUTION_COLS]

    # trades.jsonl: top-level keys mirror TradeRecord except the sniper uses
    # `_logged_at` with a leading underscore. Map it to `logged_at` here.
    n_trades = 0
    for rec in _read_jsonl(logs_dir / "trades.jsonl"):
        if rec.get("event") and rec["event"] != "trade":
            continue
        normalized = {**rec, "logged_at": rec.get("_logged_at")}
        values = _pick(normalized, trade_key_names)
        placeholders = ", ".join("?" * len(trade_key_names))
        conn.execute(
            f"INSERT OR REPLACE INTO trades "
            f"({', '.join(trade_key_names)}) VALUES ({placeholders})",
            values,
        )
        n_trades += 1

    n_res = 0
    for rec in _read_jsonl(logs_dir / "resolutions.jsonl"):
        normalized = {**rec, "logged_at": rec.get("_logged_at")}
        values = _pick(normalized, res_key_names)
        placeholders = ", ".join("?" * len(res_key_names))
        conn.execute(
            f"INSERT OR REPLACE INTO resolutions "
            f"({', '.join(res_key_names)}) VALUES ({placeholders})",
            values,
        )
        n_res += 1

    conn.commit()
    conn.close()

    return {"trades": n_trades, "resolutions": n_res, "db": str(db_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ols-home", type=Path, default=DEFAULT_OLS_HOME,
                        help=f"sniper project root (default: {DEFAULT_OLS_HOME})")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help=f"output SQLite path (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args()

    result = sync(args.ols_home, args.db)
    print(
        f"sync OK | trades={result['trades']}  resolutions={result['resolutions']}\n"
        f"db: {result['db']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
