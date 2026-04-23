# Week 1 — oracle-lag-sniper demo

A pointer, not a tutorial. The [oracle-lag-sniper](https://github.com/JonathanPetersonn/oracle-lag-sniper) is a separate tool, not part of this repo. Run it **in parallel** with the paper bridge here. The two answer different questions:

- `polymarket-paper-lab` → *are sum-arb opportunities fillable?* (mechanical profit from `yes_ask + no_ask < 1`)
- `oracle-lag-sniper` → *is the Chainlink-oracle-lag signal still exploitable today, or has competition closed it?* (directional bet based on oracle-vs-spot divergence)

Backtest stats are in [`research_notes.md`](research_notes.md) — the point of running demo mode is to see whether the signal still clears on **today's** markets.

## Install (under 15 min, no keys required)

Separate venv if you like keeping it away from paper-lab's deps.

```bash
python -m venv .venv-sniper
source .venv-sniper/Scripts/activate   # Git Bash on Windows; Linux/mac: source .venv-sniper/bin/activate

pip install https://github.com/JonathanPetersonn/oracle-lag-sniper/releases/latest/download/oracle_lag_sniper-1.2.0-py3-none-any.whl

# Config. MODE=demo is the default; no Chainlink / wallet keys needed.
# Chainlink keys only matter if you flip ORACLE_SOURCE=chainlink or
# run the historical backtest pipeline.
curl -O https://raw.githubusercontent.com/JonathanPetersonn/oracle-lag-sniper/main/.env.example
cp .env.example .env
```

## Run

```bash
# Linux / macOS:
oracle-lag-sniper run

# Windows (the console-script may not expose a .exe shim on some setups):
python -m oracle_lag_sniper run
```

Demo mode uses Polymarket's public relay for oracle data, so it starts detecting and logging "would-fire" signals without ever touching a wallet.

## What to watch in the logs (5-7 days)

The decision you're trying to make is *"is this signal still live, or has the edge been arbed out?"* Track these over a rolling window:

| Thing to log | What you're looking for |
|---|---|
| Signal fire rate (#/day) | Zero per day → signal doesn't trigger in current market regime. Steady rate → live. |
| Delta at fire (%) | Should cluster around the `delta >= 0.07%` rule. Much higher → markets have already moved when the signal fires (latency tax). |
| Token price at fire ($) | Rule is `<= $0.62`. Check the distribution of observed fire prices vs this threshold. |
| Hypothetical WR (rolling 50 trades) | Backtest was 61.4%. Demo-mode "would have won" rate materially below 60% → edge compressed. |
| Time from Chainlink tick → spot divergence → fire | Competition closes this window; if fire times drift earlier, other bots are front-running the same signal. |

## Go / no-go at end of Week 1

- **WR stays ≥ 60% over 5+ days of demo:** signal is still live. Worth building around.
- **WR 55-60%:** marginal. Combine with the sum-arb fill-rate result from this repo before deciding.
- **WR < 55% or no fires:** the edge has closed. Move on; don't graduate to live money on this strategy.

The backtest's 3-week Feb-2026 dataset is the baseline — "is the lag still exploitable" is literally the question demo mode answers, so treat demo-mode WR drift as the signal, not the backtest headline.

## Where its data lives

Its own directory. Sniper logs do NOT go into this repo's `logs/` or `data/`. Inspect in the sniper's own working dir after each run.
