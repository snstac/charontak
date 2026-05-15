# Charontak

**Repository:** [github.com/snstac/charontak](https://github.com/snstac/charontak)

```sh
git clone https://github.com/snstac/charontak.git
# or (SSH)
git clone git@github.com:snstac/charontak.git
```

Charontak is a [PyTAK](https://github.com/snstac/pytak)-based **Cursor on Target (CoT) bridge**: it relays CoT traffic between heterogeneous TAK endpoints (different transports, subnets, or trust zones), similar in spirit to a cross-domain relay or guarded “lane” across a separation boundary.

**Important:** Charontak is **software forwarding**. It is **not** a hardware data diode. For strict one-way policies use **forward-only lanes**, network segmentation, and operational controls.

## Install

```sh
pip install .
# protobuf CoT payloads (optional, matches PyTAK):
pip install '.[with_takproto]'
```

## Configuration

INI file with optional global `[charontak]` and one or more `[lane:*]` sections. Each lane declares an **ingress** and **egress** PyTAK `COT_URL` pair and a **mode**:

| Mode     | Traffic                                        |
|---------|-------------------------------------------------|
| `forward` | Ingress → egress only                           |
| `reverse` | Egress → ingress only                         |
| `duplex`  | Both directions (ensure no feedback loops)   |

See [`examples/charontak.ini`](examples/charontak.ini). PyTAK URL schemes and TLS options follow [PyTAK configuration](https://pytak.rtfd.io/).

## CLI

```sh
charontak --config /etc/charontak.ini
```

Environment overrides (same as PyTAK-style tooling):

| Variable           | Purpose                          |
|--------------------|----------------------------------|
| `CHARONTAK_CONFIG` | Default path if `--config` omitted |
| `DEBUG`           | Verbose logs when truthy           |

Logging goes to stderr; under **systemd** use `journalctl -u charontak`.

## systemd

Example unit: [`systemd/charontak.service`](systemd/charontak.service). Optional defaults for `CHARONTAK_CONFIG`: [`examples/charontak.default`](examples/charontak.default) (install as `/etc/default/charontak`).

```sh
sudo install -Dm644 systemd/charontak.service /etc/systemd/system/charontak.service
sudo install -Dm644 examples/charontak.ini /etc/charontak.ini
sudo install -Dm644 examples/charontak.default /etc/default/charontak
sudo systemctl daemon-reload
sudo systemctl enable --now charontak
```

Point `ExecStart` at the `charontak` binary from your venv, `pip install --user`, or `pipx` if it is not in `/usr/local/bin`.

## Cockpit UI

Minimal app in [`cockpit-charontak/`](cockpit-charontak/); see [`cockpit-charontak/README.md`](cockpit-charontak/README.md).

```sh
(cd cockpit-charontak && sudo make install)
```

Reload Cockpit to open **Charontak** from the tools menu.

## Deployment

- **Docker / Compose**: [`deploy/docker/README.md`](deploy/docker/README.md) — image runs Charontak plus Cockpit on port **9090** (`cockpit-ws --no-tls`; use a reverse proxy in production). Use Compose profile **`hostnet`** on Linux when bridging **multicast** CoT.
- **Ansible**: [`ansible/README.md`](ansible/README.md) — `charontak_deploy=docker` for the Compose stack, or **`native`** for cockpit + pip install on the host (better match for [AryaOS](https://github.com/snstac/aryaos)-style gateways).

## References

- [PyTAK](https://github.com/snstac/pytak/)
- Example ecosystem pattern: [ADSBCOT](https://github.com/snstac/adsbcot/) + [cockpit-adsbcot](https://github.com/snstac/cockpit-adsbcot/)
