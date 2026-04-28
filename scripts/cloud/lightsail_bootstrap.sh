#!/bin/bash
# Lightsail launch script for oracle-lag-sniper (demo mode).
#
# Paste this entire file into the "Launch script" field when creating a
# Lightsail Ubuntu 22.04 instance. It runs as root on first boot, sets
# up Python, installs the sniper wheel into a clean venv, writes a
# demo-mode .env, registers a systemd unit that auto-restarts, and
# kicks the daemon off.
#
# After ~90 seconds the instance is running and the sniper is logging
# to /home/ubuntu/oracle-lag-sniper/var/logs/.
#
# Verify after SSH:
#   systemctl status oracle-lag-sniper
#   tail -f /home/ubuntu/oracle-lag-sniper/var/logs/events.jsonl
#
# Bootstrap log lives at /var/log/sniper-launch.log if anything fails.

set -euo pipefail
exec > /var/log/sniper-launch.log 2>&1

echo "=== sniper bootstrap starting at $(date) ==="

SNIPER_HOME=/home/ubuntu/oracle-lag-sniper

# 1. Install Python 3.11 (Ubuntu 22.04 ships with 3.10; sniper wheel needs >=3.11)
apt-get update -y
apt-get install -y software-properties-common curl git
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y python3.11 python3.11-venv

# 2. OLS_HOME directory
mkdir -p "$SNIPER_HOME/var/logs"
chown -R ubuntu:ubuntu "$SNIPER_HOME"

# 3. Install wheel into a clean Python 3.11 venv as the ubuntu user.
#    rm -rf first so re-runs after a failed bootstrap start clean.
sudo -u ubuntu bash <<'USERSCRIPT'
set -euo pipefail
cd /home/ubuntu
rm -rf .sniper-venv
python3.11 -m venv .sniper-venv
source .sniper-venv/bin/activate
pip install --upgrade pip
pip install https://github.com/JonathanPetersonn/oracle-lag-sniper/releases/latest/download/oracle_lag_sniper-1.2.0-py3-none-any.whl
USERSCRIPT

# 4. Write demo-mode .env
cat > "$SNIPER_HOME/.env" <<'EOF'
MODE=demo
POLY_PRIVATE_KEY=
ORACLE_SOURCE=polymarket
OLS_HOME=/home/ubuntu/oracle-lag-sniper
ASSETS=btc,eth,xrp,sol
NOTIONAL_PER_TRADE=5.0
DEMO_SLIPPAGE=0.02
COMMENTS_ENABLED=false
REDEEM_ENABLED=false
EOF
chown ubuntu:ubuntu "$SNIPER_HOME/.env"

# 5. systemd unit
cat > /etc/systemd/system/oracle-lag-sniper.service <<'EOF'
[Unit]
Description=Oracle Lag Sniper (demo mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/oracle-lag-sniper
EnvironmentFile=/home/ubuntu/oracle-lag-sniper/.env
ExecStart=/home/ubuntu/.sniper-venv/bin/oracle-lag-sniper run
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/oracle-lag-sniper/var/logs/stdout.log
StandardError=append:/home/ubuntu/oracle-lag-sniper/var/logs/stderr.log

[Install]
WantedBy=multi-user.target
EOF

# 6. Enable + start
systemctl daemon-reload
systemctl enable oracle-lag-sniper
systemctl start oracle-lag-sniper

echo "=== sniper bootstrap complete at $(date) ==="
sleep 3
systemctl status oracle-lag-sniper --no-pager || true
