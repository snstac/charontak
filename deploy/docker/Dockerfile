# COTBridge + Cockpit admin UI (supervisord under tini).
# Build from repository root:
#   docker build -f deploy/docker/Dockerfile .

ARG BASE_IMAGE=debian:bookworm-slim

FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      dbus-daemon \
      dbus \
      cockpit-bridge \
      cockpit-ws \
      python3 \
      python3-pip \
      supervisor \
      tini \
    && rm -rf /var/lib/apt/lists/*

# Debian bookworm: allow pip to install the app system-wide in the image.
ENV PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /src
COPY pyproject.toml README.md /src/
COPY cotbridge /src/cotbridge/

RUN pip3 install --no-cache-dir .

COPY cockpit-cotbridge/manifest.json cockpit-cotbridge/index.html cockpit-cotbridge/cotbridge.js \
  /usr/share/cockpit/cotbridge/
COPY deploy/docker/cotbridge.container.ini /etc/cotbridge.ini

COPY deploy/docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY deploy/docker/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -fsS http://127.0.0.1:9090/ >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/docker-entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
