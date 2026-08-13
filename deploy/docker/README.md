# Docker: COTBridge + Cockpit

Image runs **supervisord** (under `tini`): **dbus** (system bus), **`cotbridge`**, and **`cockpit-ws`** on **port 9090** with **`--no-tls`**. Terminate TLS in front with a reverse proxy for anything beyond lab use.

## Build

From repository root:

```sh
docker build -f deploy/docker/Dockerfile -t cotbridge:local .
```

Or from this directory via Compose:

```sh
docker compose --profile bridge build
```

### Base image override

```sh
docker build -f deploy/docker/Dockerfile --build-arg BASE_IMAGE=debian:bookworm-slim -t cotbridge:local .
```

## Compose

This directory defaults to `./cotbridge.override.ini` (idle config, no enabled lanes). Copy `examples/cotbridge.ini` or edit the override before enabling bridging.

### Bridge network (TCP-friendly tests)

```sh
docker compose --profile bridge up -d
```

Cockpit: `http://127.0.0.1:9090/` (no TLS).

### Host network (multicast mesh / LAN CoT)

Docker bridge/NAT does not carry typical IPv4 multicast. For `udp://239.x.x.x` lanes:

```sh
docker compose --profile hostnet up -d
```

On **host networking**, publish **9090** by listening on `0.0.0.0` inside the container (already configured). Firewall the host appropriately.

### Config bind mount

Set `COTBRIDGE_CONFIG_HOST` to an absolute host path **before** overriding in compose if you keep a config elsewhere:

```sh
COTBRIDGE_CONFIG_HOST=/srv/cotbridge.ini docker compose ...
```

(Edit `docker-compose.yml` accordingly; the bundled file uses `./cotbridge.override.ini`.)

## Operational notes

- **First login**: set a Linux user password (`docker exec`) or rely on distro defaults; cockpit auth semantics still apply inside the OS image.
- **Host integration**: Prefer **bare-metal** cockpit + **cotbridge** on [AryaOS](https://github.com/snstac/aryaos)-class images for production; this stack is oriented at labs and repeatable demos (see Ansible `native` deployment).
- **Healthcheck**: curls `http://127.0.0.1:9090/`; behind TLS proxy, customize or disable the image `HEALTHCHECK`.
