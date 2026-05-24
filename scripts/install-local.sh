#!/usr/bin/env bash
# Install charontak CLI + Cockpit plugin (requires root for /usr paths).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Installing charontak Python package to /usr ..."
python3 -m pip install --break-system-packages .

echo "Installing Cockpit plugin to /usr/share/cockpit/charontak ..."
make -C cockpit-charontak install PREFIX=/usr

echo "Installing systemd unit + defaults (optional) ..."
install -Dm644 systemd/charontak.service /etc/systemd/system/charontak.service
install -Dm644 examples/charontak.default /etc/default/charontak
if [[ ! -f /etc/charontak.ini ]]; then
  install -Dm644 packaging/charontak.ini.example /etc/charontak.ini
fi

systemctl daemon-reload
echo "Done. Open Cockpit → Tools → Charontak (reload browser if needed)."
echo "Start bridge: sudo systemctl enable --now charontak"
