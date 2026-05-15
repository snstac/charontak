"""CLI entry for charontak."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from charontak.bridge import run_all
from charontak.config import truthy, build_lane_specs, default_config_path, load_config_parser


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
        sys.exit(1)

    globals_, lanes = build_lane_specs(cp)
    if truthy(globals_.get("debug")):
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        asyncio.run(run_all(lanes))
    except KeyboardInterrupt:
        logging.getLogger("charontak").info("Interrupted.")
        sys.exit(130)
