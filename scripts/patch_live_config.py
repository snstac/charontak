#!/usr/bin/env python3
"""Patch live /etc/cotbridge.ini: disable tcp+ppt lane, keep mesh-to-tak."""
from pathlib import Path

path = Path("/etc/cotbridge.ini")
lines = path.read_text(encoding="utf-8").splitlines()
out: list[str] = []
in_tcp = False
inserted_note = False
for line in lines:
    stripped = line.strip()
    if stripped == "[lane:tcp-to-tak]":
        in_tcp = True
        out.append(line)
        if not inserted_note:
            out.append(
                "# Disabled: PyTAK 7.x has no tcp+ppt (TCP listen). "
                "Use tcp:// as outbound client or feed udp+ro:// mesh."
            )
            inserted_note = True
        continue
    if stripped.startswith("[lane:") and stripped != "[lane:tcp-to-tak]":
        in_tcp = False
    if in_tcp and stripped == "enabled = true":
        line = "enabled = false"
    out.append(line)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated", path)
