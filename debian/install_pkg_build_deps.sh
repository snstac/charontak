#!/bin/bash
# Debian package build dependencies (pattern from snstac/pytak).

set -euo pipefail

echo "Installing Debian package build dependencies"

apt-get update -qq

apt-get install -y \
  build-essential \
  fakeroot \
  python3 \
  python3-dev \
  python3-pip \
  python3-venv \
  python3-all \
  dh-python \
  debhelper \
  devscripts \
  dput \
  software-properties-common \
  python3-distutils \
  python3-setuptools \
  python3-wheel \
  python3-stdeb
