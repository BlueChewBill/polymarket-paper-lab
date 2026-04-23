# Chainlink-Aware Directional Signal Stack

**Context:** Design doc for a Polymarket 5-minute BTC Up/Down trading bot. The bot doesn't try to predict BTC — it stacks 11 independent signal sources into a filter that identifies windows where the outcome is mechanically biased and the Polymarket price hasn't caught up.

---

## The core thesis

Market makers on Polymarket's 5-min BTC market price tokens based on current BTC delta from window open. They're already good at this — spreads are $0.01 and sums pin to exactly $1.00.

**The edge isn't out-predicting BTC.** It's combining several independent signals, any one of which is small, into a composite filter that says "don't trade most windows, but this one is mechanically tilted." MMs price each signal too, but they have to quote both sides. You're taking one side selectively. That asymmetry is the retail edge.

**This isn't a prediction engine. It's a filter stack.**

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  DATA INGESTION                       │
├──────────────────────────────────────────────────────┤
│ • Binance WebSocket (BTC/USDT + ETH/USDT 1s ticks)   │
│ • Chainlink BTC/USD on-chain reads (every 2s poll)   │
│ • Polymarket CLOB WebSocket (token prices/books)     │
│ • Exchange funding rates API (every 60s refresh)     │
│ • Deribit options OI snapshot (once/hour near exp)   │
└──────────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────┐
│           SIGNAL COMPUTATION (11 signals)             │
├──────────────────────────────────────────────────────┤
│ Tier 1 (core):  S1 S2 S3 S9 S7                        │
│ Tier 2 (adds):  S5 S6 S11 S2a                         │
│ Tier 3 (adv):   S4 S8                                 │
└──────────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────┐
│                 COMPOSITE DECISION                    │
├──────────────────────────────────────────────────────┤
│ Hard gates: all Tier-1 signals must agree on dir     │
│ Tier-2: modifies confidence up or down                │
│ Tier-3: can veto high-conviction trades in edge cases │
└──────────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────┐
│                   EXECUTION                           │
├──────────────────────────────────────────────────────┤
│ • Enter T-20 to T-10s                                 │
│ • Only fire when token ≤ $0.78 (fee-aware)            │
│ • Fractional-Kelly sizing, hard cap 2% per trade      │
└──────────────────────────────────────────────────────┘
```

---

# Signal Inventory

Each signal is rated on:
- **Edge** — how much it moves win rate vs. random
- **Cost** — what it takes to implement
- **Reliability** — how often the pattern shows up in live data

---

## TIER 1 — CORE SIGNALS (must-have for v1)

### S1: Time-of-day seasonality

**Thesis:** Crypto 5-minute moves have different statistical properties by session. Mean reversion is stronger in thin hours; momentum is stronger when liquidity is deep.

**Mechanism:** In US cash hours, directional moves come from informed institutional flow that persists. In Asian overnight hours, most moves are noise from thin order books and tend to mean-revert. This is documented in crypto intraday seasonality literature.

**Detection:**

```python
def classify_regime(utc_hour: float, weekday: int) -> str:
    """weekday: 0=Monday ... 6=Sunday"""
    if weekday >= 5:
        return "weekend"
    if 13.5 <= utc_hour < 15:     # 09:30–11:00 ET
        return "us_open"
    if 15 <= utc_hour < 19:       # 11:00–15:00 ET
        return "us_mid"
    if 19 <= utc_hour < 20.5:     # 15:00–16:30 ET
        return "us_close"
    if 20.5 <= utc_hour or utc_hour < 0:
        return "us_evening"
    if 0 <= utc_hour < 8:         # 20:00 ET – 04:00 ET
        return "asian"
    if 7 <= utc_hour < 9:
        return "london_open"
    return "pre_us"
```

**Use in decision:** Scales the confidence multiplier on directional signals. Asian/weekend regimes weight mean-reversion signals higher; US cash regimes weight momentum higher.

**Implementation cost:** 30 minutes. No new dependencies.

**Edge size:** Moderate. ~3-5 percentage points on win rate when combined with other signals.

**Caveats:** Regime boundaries are fuzzy. Holidays break everything. Treat the first market day after a US holiday as "weekend-like" behavior.

---

### S2: Cross-asset lead-lag (BTC ↔ ETH)

**Thesis:** BTC, ETH, and SOL correlate at ~0.8+ on 1-minute bars, but they don't move in perfect lockstep. When they disagree on direction, one is leading and one is lagging — the lag often indicates an imminent reversal to reconverge.

**Mechanism:** Different exchanges have different dominant asset pairs. Flow hitting Coinbase BTC shows in BTC first; flow hitting Binance ETH shows there first. When BTC is up 0.05% but ETH is down 0.08% in the same window, the second-to-move tends to catch up to the first — typically within 30-60 seconds.

**Detection:**

```python
def cross_asset_signal(btc_delta: float, eth_delta: float) -> dict:
    btc_dir = 1 if btc_delta > 0 else -1 if btc_delta < 0 else 0
    eth_dir = 1 if eth_delta > 0 else -1 if eth_delta < 0 else 0
    
    if abs(btc_delta) < 0.02 or abs(eth_delta) < 0.02:
        return {"direction": "flat", "confidence": 0.0, "agree": False}
    
    agree = (btc_dir == eth_dir)
    confidence = min(abs(btc_delta), abs(eth_delta)) / 0.15
    
    return {
        "direction": "up" if btc_dir > 0 else "down",
        "confidence": min(confidence, 1.0),
        "agree": agree,
    }
```

**Use in decision:** Hard filter. If BTC and ETH disagree at T-15s, skip the window. Cuts trades by ~40% but lifts win rate by ~5-10 points.

**Implementation cost:** 1-2 hours. Requires a second Binance WS subscription for ETHUSDT.

**Edge size:** Strong when combined with oracle signals.

**Caveats:** During correlation breakdowns (ETH-specific news, Solana outages), this filter disables itself. Track 30-min rolling correlation and skip the filter when corr drops below 0.5.

---

### S3: Chainlink oracle cycle — THE MAIN EDGE

**Thesis:** Polymarket's BTC 5-min markets resolve on Chainlink's BTC/USD feed, which updates on a discrete schedule (~10-30s, or on ~0.5% deviation). MMs and retail both watch Binance (continuous), but resolution comes from Chainlink (stepped). This creates windows where the outcome is mechanically locked but the market is still trading both sides.

**Mechanism:** At T-15s, suppose Chainlink's last print was 25s ago at $83,180 (above window-open of $83,000). Binance is at $83,175 wobbling within 5bps. Market sees "barely up" and prices Up at $0.75. But the resolving Chainlink price is already above window-open, and if Chainlink doesn't tick again before close, Up is deterministic. **True probability is ~92%, market has it at 75%.**

**Detection:**

```python
@dataclass
class OracleState:
    last_price: float
    last_update_ts: float
    window_open_price: float
    update_interval_mean: float = 15.0  # empirical Chainlink cadence
    
    def is_biased(self, now: float, window_close_ts: float) -> dict:
        seconds_since = now - self.last_update_ts
        seconds_to_close = window_close_ts - now
        next_expected = self.last_update_ts + self.update_interval_mean
        
        if next_expected > window_close_ts:
            lock_probability = 0.80
        elif seconds_to_close < 5:
            lock_probability = 0.60
        else:
            lock_probability = 0.20
        
        if self.last_price > self.window_open_price:
            direction = "up"
        elif self.last_price < self.window_open_price:
            direction = "down"
        else:
            direction = "flat"
        
        return {
            "direction": direction,
            "lock_probability": lock_probability,
            "seconds_since_update": seconds_since,
            "seconds_to_close": seconds_to_close,
        }
```

**Use in decision:** The primary edge source. When `lock_probability > 0.70` and other signals agree, this is the setup you wait for.

**Implementation cost:** 4-6 hours. Needs:
- Polygon RPC connection (Alchemy or Infura free tier)
- Read BTC/USD aggregator contract `0xc907E116054Ad103354f2D350FD2514433D57F6f` every 2s
- Track last update timestamp and price

**Edge size:** Strong. This is the mechanical edge retail can actually exploit.

**Caveats:**
- Chainlink cadence varies. Track empirical distribution; don't trust the mean.
- High volatility triggers more frequent updates (0.5% deviation rule) — lock probability drops.
- HTTP data-streams API is an alternative if on-chain reads are too slow.

---

### S9: Binance-Chainlink lag (paired with S3)

**Thesis:** Chainlink aggregates from multiple sources with inherent delay. Binance spot typically leads Chainlink BTC/USD by 1-3 seconds on sharp moves. **When Binance shows a new level, Chainlink will likely propagate it within the next cycle** — so you can position before the oracle catches up.

**Mechanism:** Suppose Binance just spiked from $83,100 to $83,280 (+0.2%) in 5 seconds. Chainlink is still showing $83,100 (8s since last update). Either Chainlink publishes before window close (Up resolves) or doesn't (Down resolves from stale price). If the move is >0.5%, Chainlink's deviation threshold fires almost immediately — you know Up will resolve.

**Detection:**

```python
def binance_chainlink_lag_signal(
    binance_price: float,
    chainlink_price: float,
    window_close_ts: float,
) -> dict:
    now = time.time()
    divergence_pct = (binance_price - chainlink_price) / chainlink_price * 100
    secs_to_close = window_close_ts - now
    
    # Large divergence → Chainlink deviation threshold will fire
    if abs(divergence_pct) > 0.4:
        return {
            "direction": "up" if divergence_pct > 0 else "down",
            "conviction": 0.9,
            "reason": "deviation_trigger_imminent",
        }
    
    # Moderate divergence + time remaining → moderate signal
    if abs(divergence_pct) > 0.15 and secs_to_close > 10:
        return {
            "direction": "up" if divergence_pct > 0 else "down",
            "conviction": 0.6,
            "reason": "binance_leading",
        }
    
    return {"direction": "flat", "conviction": 0.0, "reason": "no_divergence"}
```

**Use in decision:** Combines with S3. If divergence > 0.4%, Chainlink deviation will fire → direction highly predictable. Strongest single-signal setup.

**Implementation cost:** Minimal once S3 is built — you already have both price feeds.

**Edge size:** Strong when divergence is large. Most of the time divergence is small and the signal is flat.

**Caveats:** Deviation threshold varies by feed. Verify on the Polygon aggregator contract. If Binance shows a wick that reverts before propagating, you lose — don't trust divergences older than 3 seconds.

---

### S7: Weekend regime

**Thesis:** Saturday/Sunday crypto microstructure is materially different. Volume is lower, spreads are wider, and short-term moves are more often noise than signal.

**Mechanism:** Institutional desks are closed. Retail dominates flow. MMs still quote but with wider risk premia. Directional bets are worse on weekends — mean reversion dominates.

**Detection:**

```python
def is_weekend(utc_timestamp: float) -> bool:
    return datetime.utcfromtimestamp(utc_timestamp).weekday() >= 5
```

**Use in decision:** Disable momentum-style signals on weekends. Weight mean-reversion (S11) higher. Consider raising the conviction threshold for trades.

**Implementation cost:** Trivial. One boolean.

**Edge size:** Real but indirect. Prevents weekend losses more than it creates weekend wins.

**Caveats:** Major news can break weekend patterns (Saturday CPI print, Friday executive order). If Binance 24h volume >80% of trailing weekly average on a weekend, treat as weekday.

---

## TIER 2 — ENHANCEMENT SIGNALS (add in v2)

### S11: Small-move mean reversion

**Thesis:** Within a 5-min window, BTC moves smaller than ~0.05% are statistically noise. The market often over-prices direction (e.g., $0.55/$0.45 on +0.02%), when true probability is closer to $0.52/$0.48. Fading the lean captures ~3 cents.

**Mechanism:** MMs have a simple price-to-probability curve (per the observed delta model: +0.02% → ~$0.55 Up). For small moves, this mapping is too aggressive — actual reversal probability is higher than implied.

**Detection:**

```python
def small_move_reversion(btc_delta_pct: float, up_price: float, 
                         seconds_to_close: int) -> Optional[str]:
    if abs(btc_delta_pct) > 0.05:
        return None
    if seconds_to_close > 90:
        return None
    
    if btc_delta_pct > 0 and up_price > 0.58:
        return "down"  # fade overvalued up
    if btc_delta_pct < 0 and up_price < 0.42:
        return "up"    # fade overvalued down
    return None
```

**Use in decision:** Contrarian modifier. When Tier-1 signals are weak AND market has priced direction >58/42 on a small move, consider a small contrarian position.

**Implementation cost:** 1 hour. Uses existing data.

**Edge size:** Small but consistent. Adds ~1-2 points to win rate when it fires.

**Caveats:** Fade-against-consensus occasionally catches a momentum spike right before close. Size fade trades smaller than core directional bets.

---

### S5: Funding rate extremes

**Thesis:** When perpetual funding reaches extreme values (long or short crowded), short-term liquidation risk increases in the counter direction. This pressure leaks into 5-min windows at the margins.

**Mechanism:** Binance/Bybit perpetuals have 8-hour funding cycles. Funding >0.05% means longs crowded and paying shorts heavily. Small downticks trigger long liquidations that cascade. Windows during these regimes have slight downward skew.

**Detection:**

```python
FUNDING_EXTREME_POS = 0.05   # % per 8h
FUNDING_EXTREME_NEG = -0.03  # Negative funding is less common

def funding_signal(current_funding_pct: float) -> dict:
    if current_funding_pct > FUNDING_EXTREME_POS:
        return {"skew": -0.03, "reason": "crowded_longs"}
    if current_funding_pct < FUNDING_EXTREME_NEG:
        return {"skew": +0.04, "reason": "crowded_shorts"}
    return {"skew": 0.0, "reason": "balanced"}
```

**Use in decision:** Adjust win-probability estimate by the skew amount. If raw P(up) = 0.85 and funding skew is -0.03, revise to 0.82. Meaningful on threshold trades.

**Implementation cost:** 1-2 hours. Poll `https://fapi.binance.com/fapi/v1/premiumIndex` every 60s. Free, no auth.

**Edge size:** Moderate. ~1-3 points at extremes, nothing in the middle.

**Caveats:** Funding can be extreme for days without a cascade. Treat as modifier only, not primary signal.

---

### S6: Volume profile (within window)

**Thesis:** Abnormally high window volume = trend (direction persists). Abnormally low = noise (direction likely reverts).

**Mechanism:** High relative volume = informed flow actively taking a side. Low relative volume = nobody engaged, price action is random walk.

**Detection:**

```python
def volume_profile_signal(window_volume_so_far: float,
                          trailing_1h_avg_5min: float,
                          seconds_elapsed: float) -> dict:
    # Project full-window volume
    projected = (window_volume_so_far / seconds_elapsed) * 300
    ratio = projected / trailing_1h_avg_5min
    
    if ratio > 2.0:
        return {"regime": "trend", "conviction_mod": +0.10}
    if ratio < 0.3:
        return {"regime": "noise", "conviction_mod": -0.15}
    return {"regime": "normal", "conviction_mod": 0.0}
```

**Use in decision:** Modifies confidence in directional signals. High volume → trust direction more; low volume → fade small leans (pairs with S11).

**Implementation cost:** 2-3 hours. Maintain trailing 1-hour volume buffer from Binance WS. Straightforward but requires state.

**Edge size:** Strong when it disagrees with direction. "Direction + low volume" often reverts; "direction + high volume" often trends.

**Caveats:** Volume spikes happen for weird reasons (promos, API glitches). Validate against Coinbase before weighting high.

---

### S2a: Round-number magnetism

**Thesis:** BTC has documented attraction to round-number levels ($80K, $85K, $83,500). Price drifts toward round levels and often rejects just past them.

**Mechanism:** Retail stop orders, algo targets, and media narratives all anchor on round numbers. When price is within $30-50 of a major round level, it tends to either touch-and-reject or punch through with increased volume.

**Detection:**

```python
def round_number_signal(current_price: float) -> dict:
    nearest_500 = round(current_price / 500) * 500
    nearest_1000 = round(current_price / 1000) * 1000
    dist_500 = abs(current_price - nearest_500)
    dist_1000 = abs(current_price - nearest_1000)
    
    if dist_1000 < 30:
        pull = "up" if current_price < nearest_1000 else "down"
        return {"magnet_strength": "strong", "pull_direction": pull,
                "target": nearest_1000, "distance": dist_1000}
    if dist_500 < 20:
        pull = "up" if current_price < nearest_500 else "down"
        return {"magnet_strength": "moderate", "pull_direction": pull,
                "target": nearest_500, "distance": dist_500}
    return {"magnet_strength": "none"}
```

**Use in decision:** Secondary modifier. When BTC is $20 below $84,000 at T-30s, expect touch-and-reject. Most useful for filter overrides — if magnet direction strongly conflicts with other signals, skip the window.

**Implementation cost:** 1 hour. No new dependencies.

**Edge size:** Small but real. ~1-2 points when it fires.

**Caveats:** Doesn't work in strong trend regimes — round numbers get punched through. Combine with volume profile — weak magnets in high-vol regimes, strong in low-vol.

---

## TIER 3 — ADVANCED / MARGINAL (v3 or skip)

### S4: Options gamma pinning

**Thesis:** On Deribit option expiry days (Fridays 8AM UTC), BTC tends to pin near max-pain strike as dealer gamma hedging pulls price toward where most options expire worthless.

**Mechanism:** Options MMs are net short gamma before expiry. They hedge by buying BTC on dips and selling on rallies — dampening volatility and pulling price toward max pain. Effect strongest in the last few hours before 8AM UTC Friday.

**Detection:** Complex. Requires:
1. Deribit API pull of all BTC options OI by strike, expiring at nearest 8AM UTC Friday
2. Computing max-pain strike (where combined put+call payoff to holders is minimized)
3. Calculating dealer net gamma by strike
4. Measuring current distance from max pain

**Use in decision:** Only applies Thursday evening → Friday morning UTC. In that window:
- BTC above max pain → slight downward bias
- BTC below max pain → slight upward bias
- Pinning strongest in last 4 hours before expiry

**Implementation cost:** 2-3 days. Deribit API auth, OI parsing, max-pain computation, empirical validation.

**Edge size:** Strong in the specific window (Fri 4AM-8AM UTC), but that's ~4 hours/week. Average contribution across a full trading week is minimal.

**Caveats:** At $500 bankroll, probably not worth the engineering time. Effect is real but meaningful for 1-2% of trading hours. Consider only after everything else is working.

---

### S8: Liquidation cascade proximity

**Thesis:** When BTC is within 0.2% of a major liquidation cluster ($100M+ of leveraged positions), a small move into that level can cascade into a much bigger move.

**Mechanism:** Platforms like Coinglass publish real-time liquidation heatmaps showing OI concentration by price. When BTC moves close to a cluster, the probability of a cascade within the next 5-15 minutes is elevated.

**Detection:** Requires a third-party data source:
- **Coinglass API** (paid: $30-150/month for real-time)
- **Hyblock Capital** (free liquidation data, limited)
- Your own computation from exchange OI (complex)

```python
def liquidation_proximity_signal(current_price: float, 
                                  liq_clusters: list) -> dict:
    """liq_clusters: [{'price': 83000, 'size_usd': 120_000_000, 'side': 'long'}, ...]"""
    for cluster in liq_clusters:
        distance_pct = abs(current_price - cluster['price']) / current_price * 100
        if distance_pct > 0.2 or cluster['size_usd'] < 50_000_000:
            continue
        
        # Long liqs happen below long entries → cascade DOWN
        # Short liqs happen above short entries → cascade UP
        cascade_direction = "down" if cluster['side'] == 'long' else "up"
        return {
            "cascade_likely": True,
            "direction": cascade_direction,
            "cluster_size": cluster['size_usd'],
            "distance_pct": distance_pct,
        }
    return {"cascade_likely": False}
```

**Use in decision:** When detected, high-conviction directional override. Can override disagreeing Tier-1 signals temporarily.

**Implementation cost:** Primarily data cost ($30-150/mo). Code is straightforward.

**Edge size:** Very strong when it fires. Cascades only happen a few times per week in meaningful size.

**Caveats:** At $500 bankroll, the data subscription is a prohibitive % of capital. Skip unless you've scaled up. Hyblock free tier provides rough signals with lag.

---

# Composite Decision Logic

## Putting it all together

```python
def composite_decision(
    regime: str,                     # S1
    is_weekend: bool,                # S7
    cross_asset: dict,               # S2
    oracle_state: dict,              # S3
    binance_lag: dict,               # S9
    small_move_revert: Optional[str],# S11
    funding_skew: float,             # S5
    volume_regime: dict,             # S6
    round_magnet: dict,              # S2a
    # S4/S8 only if operational
    token_prices: dict,              # {"up": 0.72, "down": 0.28}
    seconds_to_close: float,
    bankroll: float,
) -> Optional[Trade]:
    # HARD GATE 1: Core Tier-1 agreement
    if not cross_asset["agree"]:
        return None
    if oracle_state["lock_probability"] < 0.60:
        return None
    if oracle_state["direction"] != cross_asset["direction"]:
        return None
    
    # HARD GATE 2: Timing window
    if seconds_to_close > 30 or seconds_to_close < 5:
        return None
    
    direction = oracle_state["direction"]
    base_confidence = 0.80
    
    # S1 regime modifier
    if regime in ("asian",):
        base_confidence -= 0.03
    elif regime == "us_open":
        base_confidence += 0.03
    
    # S7 weekend modifier
    if is_weekend:
        base_confidence -= 0.05
    
    # S9 Binance-Chainlink boost
    if binance_lag.get("reason") == "deviation_trigger_imminent":
        base_confidence = min(base_confidence + 0.08, 0.95)
    
    # S6 volume profile
    base_confidence += volume_regime.get("conviction_mod", 0)
    
    # S5 funding skew (asymmetric directional adjust)
    funding_adj = funding_skew if direction == "up" else -funding_skew
    base_confidence += funding_adj
    
    # S2a round-number veto
    if (round_magnet.get("magnet_strength") == "strong" and 
        round_magnet.get("pull_direction") != direction):
        return None
    
    # S11 small-move revert veto
    if small_move_revert and small_move_revert != direction:
        return None
    
    # Price check — fee-aware
    token_price = token_prices[direction]
    if token_price > 0.78:
        return None
    if token_price < 0.50:
        return None
    
    # Size with fractional Kelly
    size_usd = kelly_size(token_price, base_confidence, bankroll)
    if size_usd < 2.5:
        return None
    
    return Trade(direction=direction, size=size_usd, price=token_price,
                 confidence=base_confidence)
```

## Position sizing (fee-aware Kelly)

```python
TAKER_FEE = 0.072  # Polymarket crypto_fees_v2

def kelly_size(token_price: float, est_win_prob: float, 
               bankroll: float, kelly_frac: float = 0.25) -> float:
    if est_win_prob <= token_price:
        return 0
    
    gross_win_payoff = (1 - token_price) / token_price
    net_win_payoff = gross_win_payoff * (1 - TAKER_FEE)
    
    edge = est_win_prob * net_win_payoff - (1 - est_win_prob)
    if edge <= 0:
        return 0
    
    kelly_fraction = edge / net_win_payoff
    position_pct = min(kelly_fraction * kelly_frac, 0.02)
    return round(bankroll * position_pct, 2)
```

---

# Data Sources

| Source | Endpoint | Cost | Used by |
|---|---|---|---|
| Binance WS spot | `wss://stream.binance.com:9443/ws` | Free | S2, S6, S9, S11 |
| Chainlink on-chain | Polygon RPC → `0xc907E116...57F6f` | Free* | S3, S9 |
| Polymarket CLOB WS | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Free | Execution |
| Polymarket Gamma | `gamma-api.polymarket.com/events` | Free | Market discovery |
| Binance Futures funding | `fapi.binance.com/fapi/v1/premiumIndex` | Free | S5 |
| Deribit options | `deribit.com/api/v2/public/get_book_summary_by_currency` | Free | S4 |
| Coinglass liqs | `open-api.coinglass.com` | $30-150/mo | S8 |

\* Alchemy/Infura free tier is fine for RPC reads at 2s cadence.

---

# Build Order

## Phase 0: Pure logging (2 days)

Before any signal code, instrument the v3 sum-arb scanner to log:
- Binance BTC/ETH prices at 1s resolution
- Chainlink BTC/USD prints with timestamps
- Polymarket Up/Down token prices at T-60, T-30, T-15, T-5, T-0
- Final resolution

Run for 48 hours. ~576 windows of data.

Analyze: at T-15s, how often does Polymarket's price agree with eventual resolution? This is your ceiling.

## Phase 1: Tier-1 signals (3-5 days)

Build S1, S2, S3, S7, S9. Paper-mode decision logic. Log every decision (fired or skipped) with all signal values.

Run 1 week. Validate hit rate.

**Kill criteria:** If validated win rate <75% at typical entry prices, system is not viable. Do not proceed.

## Phase 2: Add Tier-2 (3 days)

Add S5, S6, S11, S2a. Re-run 1-week log. Compare composite hit rate with and without each signal. Keep signals that provably add >1 point; drop the rest.

## Phase 3: Live execution (1 week, tiny size)

Integrate py-clob-client. Hard cap $2/trade. Trade 50+ windows. Compare live P&L to simulated.

**Kill criteria:** Live P&L deviating materially from simulated → pause and diagnose (slippage, latency, API quirks).

## Phase 4: Tier-3 consideration

Only after 200+ live trades and verified positive edge. S4 and S8 are opt-in based on whether ROI justifies dev time (S4) or data cost (S8).

---

# Honest Edge Assessment

## What's likely to work
- **S3 + S9 combo** — clearest mechanical edge. Oracle lag is documented and structural.
- **S2 cross-asset filter** — cuts noise by ~40% at the cost of fewer trades.
- **S7 weekend regime** — trivial and prevents a common loss mode.
- **S1 time-of-day** — subtle but additive when combined with others.

## What's questionable
- **S5 funding** — genuine signal but only at extremes.
- **S6 volume profile** — powerful but hard to get right on fast timeframes.
- **S11 mean reversion** — works but size fade trades tiny.
- **S2a round numbers** — real effect but inconsistent, needs regime context.

## What's probably not worth it at $500
- **S4 options gamma** — real but only ~4 hours/week of relevance.
- **S8 liquidation cascades** — strong signal but data cost prohibitive at this bankroll.

## Infrastructure realities
- **Latency tax is real.** Running from Round Rock adds 50-150ms per round trip vs. us-east-1 VPS. Each round trip of latency costs edge.
- **Chainlink update detection drifts.** Your "oracle locked" call has false positives; validate empirically.
- **Fee drag is silent.** 7.2% taker fees mean every win is worth ~$0.03 less than naive math. Over 500 trades → $15, which is 3% of bankroll.

## Realistic outcome distribution (full stack)
- **Best case:** 1-3% daily return, compounding. Few months → $1,500-2,500.
- **Middle case:** 0-0.5% daily, volatile, net flat after fees/slippage.
- **Worst case:** Signal miscalibration, 5-10% monthly loss. Catch early → out ~$50-100.

Best case is achievable but not guaranteed. Middle case is most likely. Worst case is real.

---

# What to build first

**Not a signal. Logging.**

Instrument the v3 sum-arb scanner to record, for every 5-min BTC window:
- Open price (Binance + Chainlink, separately)
- Close price (both)
- Count of Chainlink updates during window
- Polymarket Up token price at T-60, T-30, T-15, T-5, T-0
- Final resolution

Run 48 hours. Analyze whether Polymarket prices at T-15 already encode the outcome. That single analysis tells you whether any signal stack is worth building.

**Don't write the signals until the data says it's worth it.**

---

# Key links

- Polymarket CLOB docs: https://docs.polymarket.com
- Chainlink BTC/USD stream: https://data.chain.link/streams/btc-usd
- Chainlink Polygon aggregator: 0xc907E116054Ad103354f2D350FD2514433D57F6f
- Binance WS: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
- Binance Futures funding: https://binance-docs.github.io/apidocs/futures/en/#mark-price
- Deribit API: https://docs.deribit.com
- Coinglass: https://docs.coinglass.com
- py-clob-client: https://github.com/Polymarket/py-clob-client

---

# TL;DR

Eleven signals across three tiers. Tier 1 (5 signals) is the spine — Chainlink oracle state, cross-asset confirmation, regime filters. Tier 2 (4 signals) adds refinement from volume, funding, round-numbers, mean-reversion. Tier 3 (2 signals) is optional advanced territory that probably doesn't make sense at $500.

Only trade when **all Tier-1 signals agree**, Tier-2 boosts confidence past your entry threshold, and no signal vetoes. Fee-aware Kelly sizing, 2% hard cap per trade.

**Build logging first. Validate before trading. Kill it if the data says no.**
