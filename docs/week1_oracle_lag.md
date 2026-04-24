# Week 1 — oracle-lag-sniper demo (parallel to the paper bridge)

A pointer, not a tutorial. The [oracle-lag-sniper](https://github.com/JonathanPetersonn/oracle-lag-sniper) is a separate tool, not part of this repo. It runs in its own sibling directory, produces its own logs, and this repo has adapter scripts that reshape its output into a SQLite DB joinable with the paper-trader's `paper.db` — so both strategies can be compared side-by-side.

The two answer different questions:

- `polymarket-paper-lab` → *are sum-arb opportunities fillable?* (mechanical profit from `yes_ask + no_ask < 1`)
- `oracle-lag-sniper` → *is the Chainlink-oracle-lag signal still exploitable today, or has competition closed it?* (directional bet based on oracle-vs-spot divergence)

Backtest stats are in [`research_notes.md`](research_notes.md).

## Directory layout

```
C:\Users\dylan\polymarket\files\
├── polymarket-paper-lab\        this repo
│   ├── src/paper_bridge.py
│   ├── scripts/
│   │   ├── sync_sniper_to_sqlite.py    reshapes sniper JSONL -> SQLite
│   │   ├── compare_strategies.py       side-by-side report
│   │   └── ...
│   └── data/trades.jsonl        our sum-arb opportunity records
├── oracle-lag-sniper\           sniper's runtime dir (NOT in git)
│   ├── .env                     OLS_HOME pins logs into this dir
│   └── var/logs/
│       ├── trades.jsonl         sniper trade entries
│       ├── resolutions.jsonl    outcome + P&L per market
│       ├── signals.jsonl        every signal fire
│       └── state.json           system state snapshot
~\.pm-trader\sumarb\paper.db     pm-trader paper account (SQLite)
~\.ols-sniper\sniper.db          our adapter output (SQLite)
```

The sniper's directory is **not** committed to the paper-lab repo — it's a peer directory for a separate tool. Only the adapter scripts that read its output live in the repo.

## Install (one command, ~1 min)

Install the wheel globally for the current user so it's on PATH no matter which terminal you're in:

```bash
pip install --user https://github.com/JonathanPetersonn/oracle-lag-sniper/releases/latest/download/oracle_lag_sniper-1.2.0-py3-none-any.whl
```

The sibling directory `C:\Users\dylan\polymarket\files\oracle-lag-sniper\` is already created with a demo-mode `.env` pointing `OLS_HOME` back to itself, so logs land inside the directory rather than somewhere the tool guesses.

## Run

From the sibling directory:

```bash
cd /c/Users/dylan/polymarket/files/oracle-lag-sniper
oracle-lag-sniper run
# or, if the console-script shim isn't on PATH:
python -m oracle_lag_sniper run
```

Demo mode uses Polymarket's public relay for oracle data, no keys required. Let it run alongside `python src/paper_bridge.py` from the paper-lab repo.

## Inspect / compare

From the paper-lab repo:

```bash
# 1. Reshape the sniper's JSONL into ~/.ols-sniper/sniper.db
python scripts/sync_sniper_to_sqlite.py

# 2. Side-by-side report: paper-lab trades.jsonl vs sniper.db
python scripts/compare_strategies.py
```

The adapter is idempotent — it drops and rebuilds the SQLite each time. Safe to run while the sniper is still writing to its JSONL (WAL mode on both DBs).

Direct SQL is available once `sniper.db` exists:

```bash
sqlite3 -readonly ~/.ols-sniper/sniper.db
# views: trades, resolutions, trade_outcomes (left join of both)
# example: daily win rate
#   SELECT date(logged_at, 'unixepoch'), COUNT(*),
#          SUM(CASE WHEN resolved_pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
#     FROM trade_outcomes WHERE resolved_pnl IS NOT NULL
#     GROUP BY 1;
```

## What to watch in the logs (5-7 days)

The decision you're trying to make is *"is this signal still live, or has the edge been arbed out?"*

| Thing to log | What you're looking for |
|---|---|
| Signal fire rate (#/day) | Zero per day → signal doesn't trigger in current market regime. Steady rate → live. |
| Delta at fire (%) | Should cluster around the `delta >= 0.07%` rule. Much higher → markets have already moved when the signal fires (latency tax). |
| Token price at fire ($) | Rule is `<= $0.62`. Check distribution of observed fire prices. |
| Hypothetical WR (rolling 50 trades) | Backtest was 61.4%. Demo-mode WR materially below 60% → edge compressed. |
| Time from Chainlink tick → fire | Competition closes this window; if fire times drift earlier, other bots are front-running. |

`compare_strategies.py` bakes the WR vs 55/60% thresholds into its verdict output, so after the run you get a one-line read rather than eyeballing numbers.

## Go / no-go at end of Week 1

- **WR stays >=60% over 5+ days:** signal live. Worth building around.
- **WR 55-60%:** marginal. Factor in paper-lab's fill-rate result before deciding.
- **WR <55% or no fires:** edge closed. Move on.

## Where its data lives

The sibling directory's `var/logs/` holds the sniper's JSONL. That directory is **not** in git (nor should it be — it's runtime data for a separate tool). The adapter reads from there and writes to `~/.ols-sniper/sniper.db`, which is likewise runtime data and not in git. Both are regeneratable from the sniper's logs at any time.
