# Cockpit UI for Charontak

Minimal [Cockpit](https://cockpit-project.org/) application mirroring [cockpit-adsbcot](https://github.com/snstac/cockpit-adsbcot): systemd status and restart for `charontak.service`, journal tail, load/save `/etc/charontak.ini` via Cockpit’s file APIs (elevates when permitted).

```sh
cd cockpit-charontak
sudo make install
```

Reload Cockpit — **Charontak** appears under the tools menu.

`cockpit.file` may require polkit rules for editing `/etc/charontak.ini` under your distro; operate as admin in Cockpit.
