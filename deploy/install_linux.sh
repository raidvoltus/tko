#!/usr/bin/env bash
# Install TKO as systemd service (LIVE). Requires root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
id -u tko &>/dev/null || useradd --system --home /var/lib/tko --shell /usr/sbin/nologin tko
mkdir -p /var/lib/tko /var/log/tko /etc/tko
chown -R tko:tko /var/lib/tko /var/log/tko
if [[ ! -f /etc/tko/tko.env ]]; then
  cat > /etc/tko/tko.env <<'ENV'
TKO_LIVE_MODE=true
TKO_LOOP_INTERVAL_SEC=60
TKO_MAX_ORDER_NOTIONAL=7500000
TKO_MAX_DAILY_NOTIONAL=30000000
TKO_MAX_DAILY_LOSS_PCT=5
TKO_RISK_TIMEZONE=Asia/Jakarta
ENV
  chmod 640 /etc/tko/tko.env
  echo "Created /etc/tko/tko.env — review before start."
fi
python3 -m pip install -r "$ROOT/requirements.lock"
python3 -m pip install -e "$ROOT"
install -m 644 "$ROOT/deploy/tko.service" /etc/systemd/system/tko.service
systemctl daemon-reload
systemctl enable tko.service
echo "Installed. Run as user tko: python -m tko setup"
echo "Then: systemctl start tko && systemctl status tko"
