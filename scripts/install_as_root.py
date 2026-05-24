#!/usr/bin/env python3
"""System install for charontak + Cockpit plugin (run: sudo /usr/bin/python3 scripts/install_as_root.py)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    os.chdir(ROOT)

    print("Installing charontak with pip …")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "."],
        cwd=ROOT,
    )

    cockpit_dst = Path("/usr/share/cockpit/charontak")
    cockpit_src = ROOT / "cockpit-charontak"
    print(f"Installing Cockpit plugin → {cockpit_dst} …")
    cockpit_dst.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "index.html", "charontak.js"):
        shutil.copy2(cockpit_src / name, cockpit_dst / name)
        (cockpit_dst / name).chmod(0o644)

    print("Installing systemd unit and config …")
    shutil.copy2(ROOT / "systemd/charontak.service", Path("/etc/systemd/system/charontak.service"))
    Path("/etc/systemd/system/charontak.service").chmod(0o644)

    default_dst = Path("/etc/default/charontak")
    shutil.copy2(ROOT / "examples/charontak.default", default_dst)
    default_dst.chmod(0o644)

    ini_dst = Path("/etc/charontak.ini")
    if not ini_dst.is_file():
        shutil.copy2(ROOT / "packaging/charontak.ini.example", ini_dst)
        ini_dst.chmod(0o644)

    bin_path = Path(shutil.which("charontak") or "/usr/local/bin/charontak")
    usr_bin = Path("/usr/bin/charontak")
    if bin_path.is_file() and bin_path != usr_bin:
        usr_bin.unlink(missing_ok=True)
        usr_bin.symlink_to(bin_path)
        print(f"Linked {usr_bin} -> {bin_path}")

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "charontak.service"], check=False)
    subprocess.run(["systemctl", "restart", "charontak.service"], check=True)

    which = str(bin_path)
    print(f"charontak binary: {which}")
    print("Cockpit: reload browser → Tools → Charontak")

    import time

    time.sleep(1)
    status = subprocess.run(
        ["systemctl", "is-active", "charontak.service"],
        capture_output=True,
        text=True,
    )
    print(f"charontak.service: {status.stdout.strip() or status.stderr.strip() or 'unknown'}")
    print("--- recent startup log ---")
    subprocess.run(
        ["journalctl", "-u", "charontak.service", "-n", "20", "--no-pager"],
        check=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
