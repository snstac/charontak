#!/bin/sh
set -e
mkdir -p /run/dbus /var/run/dbus
exec "$@"
