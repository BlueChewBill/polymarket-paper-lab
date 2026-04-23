# polymarket-paper-lab

Paper-trading harness for validating Polymarket sum-arb and oracle-lag
signals against live order books. Zero real capital at risk.

## What this is

A bridge between a sum-arb scanner (detects `ask_yes + ask_no < threshold`
on crypto Up/Down markets) and [`agent-next/polymarket-paper-trader`](https://github.com/agent-next/polymarket-paper-trader).
Scanner alerts fire simulated trades through a real-order-book engine,
producing a per-trade record of:

- What the scanner claimed (top-of-book prices)
- What you'd actually fill at (walks real book levels)
- Slippage in basis points
- Final P&L when the market resolves

The question this answers: **are detected sum-arb opportunities
actually fillable, or is this a phantom-liquidity chase?**

Eventually extends to test the 11-signal oracle-aware stack in
[`docs/signal_stack.md`](docs/signal_stack.md).

## Status

Early development. Scanner refactor pending. Not trading live.
Not financial advice.

## Quick start

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
pip install -r requirements.txt
cp .env.example .env

# Optional sanity check: imports, engine init, one /events fetch. ~10s.
python scripts/health_check.py

# Run the harness. First run auto-initializes the paper account
# at INITIAL_BALANCE_USD from .env. Ctrl-C to stop; prints a session
# summary and writes data/trades.jsonl.
python src/paper_bridge.py

# After a session, bucket the JSONL into fill rate / slippage / post-fee edge.
python scripts/analyze_trades.py
```

## References

- [`agent-next/polymarket-paper-trader`](https://github.com/agent-next/polymarket-paper-trader) — the simulator this wraps
- [`JonathanPetersonn/oracle-lag-sniper`](https://github.com/JonathanPetersonn/oracle-lag-sniper) — reference oracle-lag implementation
- [`SebastianBoehler/poly-arb`](https://github.com/SebastianBoehler/poly-arb) — prior art on sum-arb (abandoned after 10 days)

## License

MIT.
