# Runbook

One-page reference for running the parallel paper lab. Keep this open in a tab.

## What is what, and where it lives

```
C:\Users\dylan\polymarket\files\
├── polymarket-paper-lab\         <- THIS REPO (git-tracked)
│   ├── src\
│   │   ├── paper_bridge.py       the main bridge
│   │   └── scanner.py            sum-arb scanner (called by bridge)
│   ├── scripts\
│   │   ├── health_check.py       pre-run sanity check (10s)
│   │   ├── analyze_trades.py     paper-lab verdict
│   │   ├── sync_sniper_to_sqlite.py   sniper JSONL -> SQLite
│   │   └── compare_strategies.py      side-by-side verdict
│   ├── data\trades.jsonl         bridge's opportunity+fill log (generated)
│   ├── logs\paper_bridge.log     bridge's text log (generated)
│   └── docs\
│       ├── runbook.md            this file
│       ├── signal_stack.md       11-signal design (future work)
│       ├── research_notes.md     API facts + backtest refs
│       └── week1_oracle_lag.md   sniper parallel-run design
│
└── oracle-lag-sniper\            <- SNIPER'S RUNTIME DIR (NOT git-tracked)
    ├── .env                      MODE=demo, OLS_HOME pinned here
    └── var\logs\                 sniper's JSONL output (generated)
        ├── trades.jsonl
        ├── resolutions.jsonl
        ├── signals.jsonl
        └── events.jsonl

C:\Users\dylan\.pm-trader\sumarb\paper.db     pm-trader account (generated)
C:\Users\dylan\.ols-sniper\sniper.db          sync adapter output (generated)
```

**What's committed to git:** everything in the paper-lab repo above.
**What's NOT in git:** `data\`, `logs\`, `.env`, the sibling sniper dir, either `.db` file.

## Start a parallel session (two terminals)

### Terminal 1 — sniper

```powershell
cd C:\Users\dylan\polymarket\files\oracle-lag-sniper
oracle-lag-sniper run
```

Runs until Ctrl-C. Expect:
- `Oracle Lag Sniper - DEMO mode` + `Starting loops...` within 5s
- `var\logs\events.jsonl` populates with `market_transition` events within 30s
- Most early events will be `active -> skipped` (signal didn't fire — expected)

### Terminal 2 — paper-lab bridge

```powershell
cd C:\Users\dylan\polymarket\files\polymarket-paper-lab
python src\paper_bridge.py
```

Runs until Ctrl-C. Expect:
- `paper account=sumarb  cash=$500.00  total=$500.00` on startup
- `tracking N Up/Down markets` every 60s as markets roll
- `OPP <slug>  sum=0.98xx  edge=+0.xx%` when a sum-arb opportunity fires
- `FILLED ...` or `REJECT ...` per opportunity

## Check progress (third terminal, any time)

```powershell
cd C:\Users\dylan\polymarket\files\polymarket-paper-lab

# Paper-lab's own verdict
python scripts\analyze_trades.py

# Sync sniper JSONL -> SQLite, then side-by-side
python scripts\sync_sniper_to_sqlite.py
python scripts\compare_strategies.py

# Live tail of any sniper log
Get-Content C:\Users\dylan\polymarket\files\oracle-lag-sniper\var\logs\events.jsonl -Tail 10 -Wait
```

## From Claude Desktop chat (MCP)

Once Claude Desktop is restarted after the MCP config edit, you can ask in chat:

- *"What's the balance on my sumarb paper account?"*
- *"Show me my trade history on sumarb"*
- *"Give me a stats card for sumarb"*
- *"What markets are closing in the next hour?"* (read-only discovery)

The MCP defaults to the `sumarb` account via `PM_TRADER_ACCOUNT` in the config.

## Stop a session

Ctrl-C in each terminal. The bridge prints a session summary (opps count, fill rate, mean slippage, cash/total) before exiting.

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `python: The term ... not recognized` | PATH change didn't take effect | **Close and reopen** the PowerShell window |
| `No such file or directory: 'src\\paper_bridge.py'` | Wrong CWD | `cd` into the repo root first |
| `ConnectionClosedError: frame ... exceeds limit` | Outdated code | Already fixed (commit 42e3f63); restart the bridge |
| `--help#` error from pm-trader | Comment glued to flag | Paste commands one at a time, or use backtick line breaks |
| Claude chat doesn't see pm-trader tools | Claude Desktop wasn't restarted | Quit from tray icon, relaunch |
| `MCP server` error on startup | `pm-trader-mcp.exe` not found | Verify `where.exe pm-trader-mcp` works in PowerShell |

## The rule for invoking things

- `oracle-lag-sniper` -- bare command, from anywhere. It's a console-script on PATH.
- `pm-trader`, `pm-trader-mcp` -- bare commands, from anywhere.
- Anything Python-script-ish (`python src\...`, `python scripts\...`) -- **always** from the paper-lab repo root. Use `cd` first.

## Where to analyze after N days

After 5-7 days of parallel running, the decision data lives in:

- `C:\Users\dylan\polymarket\files\polymarket-paper-lab\data\trades.jsonl`
- `C:\Users\dylan\.ols-sniper\sniper.db` (regenerate with `sync_sniper_to_sqlite.py`)

The two analysis scripts (`analyze_trades.py`, `compare_strategies.py`) produce a one-line verdict from each dataset. That verdict is the go/no-go signal — don't overthink it.
