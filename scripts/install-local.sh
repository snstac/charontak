#!/usr/bin/env bash
# Install cotbridge CLI + Cockpit plugin (requires root for /usr paths).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Installing cotbridge Python package to /usr ..."
python3 -m pip install --break-system-packages .

echo "Installing Cockpit plugin to /usr/share/cockpit/cotbridge ..."
make -C cockpit-cotbridge install PREFIX=/usr

echo "Installing systemd unit + defaults (optional) ..."
install -Dm644 systemd/cotbridge.service /etc/systemd/system/cotbridge.service
install -Dm644 examples/cotbridge.default /etc/default/cotbridge
if [[ ! -f /etc/cotbridge.ini ]]; then
  install -Dm644 packaging/cotbridge.ini.example /etc/cotbridge.ini
fi

systemctl daemon-reload
echo "Done. Open Cockpit → Tools → COTBridge (reload browser if needed)."
echo "Start bridge: sudo systemctl enable --now cotbridge"
