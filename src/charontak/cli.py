# Copyright 2026 Sensors & Signals LLC https://www.snstac.com/
# SPDX-License-Identifier: Apache-2.0
"""CLI entry for charontak."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from charontak.bridge import run_all
from charontak.config import truthy, build_lane_specs, default_config_path, load_config_parser, validate_lanes

LOG = logging.getLogger("charontak")

# sysexits EX_CONFIG — tell systemd not to restart on bad INI (RestartPreventExitStatus).
EXIT_CONFIG = 78


def _package_version() -> str:
    try:
        return version("charontak")
    except PackageNotFoundError:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="charontak",
        description="PyTAK CoT bridge between TAK transports (multi-lane).",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=default_config_path(),
        help=f"INI config path (default: {default_config_path()}, or env CHARONTAK_CONFIG)",
    )
    args = parser.parse_args()

    env_debug = truthy(os.environ.get("DEBUG"))
    level = logging.DEBUG if env_debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cp = load_config_parser(args.config)
    except FileNotFoundError as exc:
        logging.getLogger("charontak").error("%s", exc)
        sys.exit(EXIT_CONFIG)

    globals_, lanes = build_lane_specs(cp)
    if truthy(globals_.get("debug")):
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        validate_lanes(lanes)
    except ValueError as exc:
        LOG.error("%s", exc)
        sys.exit(EXIT_CONFIG)

    lane_word = "lane" if len(lanes) == 1 else "lanes"
    LOG.info(
        "Charontak %s — config %s (%s %s)",
        _package_version(),
        args.config,
        len(lanes),
        lane_word,
    )

    try:
        asyncio.run(run_all(lanes))
    except OSError as exc:
        LOG.error("%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        LOG.info("Interrupted.")
        sys.exit(130)
