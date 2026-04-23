# Research notes

Running log of findings while building this out.

## Reference implementations reviewed

**oracle-lag-sniper (JonathanPetersonn)** — Chainlink/orderbook latency strategy.
Rules: delta ≥ 0.07%, time ≥ 5min, token ≤ $0.62. Backtest: 61.4% WR,
$59K on 5,017 trades (BTC/ETH/XRP/SOL). 60/40 train/test, 7 falsification
tests. Demo mode via wheel install, no keys required.

**poly-arb (SebastianBoehler)** — Professional C++ sum-arb bot. Abandoned
after 10 days of active development. Last commit pivoted from sum-arb to
"dead-outcome dust probe" — strong signal that basic sum-arb wasn't clearing.
Uses wrong 2% fee assumption; actual fee is 7.2% taker on crypto_fees_v2.

**agent-next/polymarket-paper-trader** — The simulator this repo wraps.
Walks real Polymarket order book level-by-level, applies exact fee formula,
tracks slippage in bps. MCP server exposes 26 tools.

## Key API facts

- Polymarket 5-min Up/Down events live at `gamma-api.polymarket.com/events?tag_id=102127`, not `/markets`
- Fee structure is `crypto_fees_v2`: 7.2% taker, 20% maker rebate
- Resolution source for BTC 5-min markets: Chainlink BTC/USD on Polygon (`0xc907E116054Ad103354f2D350FD2514433D57F6f`)
- Chainlink update cadence: ~10-30s normally, immediate on 0.5% deviation
- Binance spot typically leads Chainlink by 1-3s on sharp moves
