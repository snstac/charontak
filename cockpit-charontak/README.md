# Cockpit UI for Charontak

Minimal [Cockpit](https://cockpit-project.org/) application mirroring [cockpit-adsbcot](https://github.com/snstac/cockpit-adsbcot): systemd status and restart for `charontak.service`, journal tail, load/save `/etc/charontak.ini` via Cockpit’s file APIs (elevates when permitted).

```sh
cd cockpit-charontak
sudo make install
```

Reload Cockpit — **Charontak** appears under the tools menu.

Load/save uses `cockpit.file(..., { superuser: "require" })` so only users who can escalate (sudo/wheel) can write `/etc/charontak.ini`. Saves pass the file tag from the last read to avoid overwriting concurrent edits.
