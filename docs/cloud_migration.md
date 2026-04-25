# Cloud migration playbook (when, where, how)

## When to migrate (not "right now")

The sniper's edge is in a 1-3 second window: Binance shows a price move, you have until Chainlink's next cron tick to position. From a residential connection in Round Rock TX, round-trip latency to Polymarket's CLOB infrastructure is ~50-150ms with multi-second tail jitter. That eats meaningful chunks of the available window.

**But — don't migrate until the sniper has shown promise locally.** A bot bleeding $0.97/trade on local infrastructure isn't going to suddenly start winning when you move it to AWS. Latency optimization is a multiplier on existing edge, not a creator of edge. Migrate only after:

1. Sniper hits **50-100 resolved trades** locally
2. WR is at least **55%+** (vs backtest 61.4%)
3. Cumulative P&L is positive or close to break-even on local

If you're at 50% WR after 100 trades on local infra, the signal is gone or compressed. No cloud server fixes that.

## Where Polymarket actually lives

Polymarket's CLOB API (`https://clob.polymarket.com`) and matching engine sit behind Cloudflare with origins in **AWS us-east-1** (N. Virginia / Ashburn). The Polymarket RTDS feed the sniper uses by default also originates from there. Polygon RPC nodes (Alchemy / Infura) similarly cluster in us-east-1 / us-east-2.

**Optimal colocation: AWS us-east-1 (Ashburn VA).** Anything else gives up cross-region latency.

## Provider options ranked

| Provider | Region | Cost | Why |
|---|---|---|---|
| **AWS Lightsail** | us-east-1 (Virginia) | $3.50-5/mo | Same physical region as Polymarket. Lowest latency. |
| **Vultr High-Frequency** | New Jersey / Newark | $6/mo | Excellent peering to AWS us-east-1. |
| **Linode Nanode** | Newark NJ | $5/mo | Reliable, ~5ms to us-east-1. |
| **DigitalOcean** | NYC3 | $4/mo | Acceptable, slightly older infra. |
| ~~Hetzner~~ | Oregon | $5/mo | **AVOID** — west-coast US adds 60-80ms cross-country. |

The Hetzner CX22 referenced in `polymarket_agents_outline.md` is a great deal **for non-latency-sensitive bots**. The sniper is latency-sensitive, so AWS / Vultr / Linode East are the right call.

**Verify before committing:** spin up the cheapest tier on two candidates, run `ping clob.polymarket.com` from each, pick the lower median + tighter jitter.

## Recommended setup

**AWS Lightsail us-east-1, Ubuntu 22.04, $5/mo (1 vCPU, 1GB RAM, 40GB SSD).**

This is plenty for the sniper. The bot is single-threaded async I/O — CPU/memory aren't the bottleneck.

## Migration steps (for future-you)

```bash
# 1. SSH in
ssh ubuntu@<your-instance-ip>

# 2. Python + system deps
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip git

# 3. Install the sniper wheel
pip install --user https://github.com/JonathanPetersonn/oracle-lag-sniper/releases/latest/download/oracle_lag_sniper-1.2.0-py3-none-any.whl

# 4. Setup OLS_HOME directory
mkdir -p ~/oracle-lag-sniper/var/logs
cd ~/oracle-lag-sniper

# 5. Copy your local .env (has MODE=demo and your config)
#    From local Windows:
#    scp C:\Users\dylan\polymarket\files\oracle-lag-sniper\.env ubuntu@<ip>:~/oracle-lag-sniper/

# 6. Set OLS_HOME to absolute path in .env
sed -i 's|^OLS_HOME=.*|OLS_HOME=/home/ubuntu/oracle-lag-sniper|' .env

# 7. Run as a systemd service so it survives reboots and SSH disconnects
sudo tee /etc/systemd/system/oracle-lag-sniper.service > /dev/null <<EOF
[Unit]
Description=Oracle Lag Sniper (demo mode)
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/oracle-lag-sniper
ExecStart=/home/ubuntu/.local/bin/oracle-lag-sniper run
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/oracle-lag-sniper/var/logs/stdout.log
StandardError=append:/home/ubuntu/oracle-lag-sniper/var/logs/stderr.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now oracle-lag-sniper
sudo systemctl status oracle-lag-sniper
```

## Monitoring from local

```bash
# Tail recent activity
ssh ubuntu@<ip> 'tail -F ~/oracle-lag-sniper/var/logs/events.jsonl'

# Pull current state
ssh ubuntu@<ip> 'cat ~/oracle-lag-sniper/var/logs/state.json' | jq

# Sync JSONL back to local for analysis
rsync -avz ubuntu@<ip>:~/oracle-lag-sniper/var/logs/ \
  C:/Users/dylan/polymarket/files/oracle-lag-sniper/var/logs/

# Then run dashboard / analysis locally as usual
python scripts/sync_sniper_to_sqlite.py
python scripts/dashboard.py
```

## Going live (eventually, if data justifies)

If the sniper survives the WR-validation gate at the cloud-hosted demo-mode stage, the path to live trading is:

1. Generate Polymarket API keys via the UI
2. Set `MODE=live` and fill `POLY_PRIVATE_KEY`, `POLY_API_KEY`, etc. in .env
3. **Start with `NOTIONAL_PER_TRADE=2`** — half the demo size — for the first 50 trades
4. Compare live fill prices vs demo's synthetic +0.02 slippage — that's the real reality check
5. Only scale up after live P&L matches demo P&L over 100+ trades

**Hard stop:** if at any point live drawdown exceeds 25% of capital allocated, kill the daemon and re-validate. Don't trust the strategy more than the data.
