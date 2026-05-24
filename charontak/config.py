"""Load INI configuration for Charontak."""

from __future__ import annotations

import os
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, Mapping, MutableMapping, Optional


GLOBAL_SECTION = "charontak"
LANE_PREFIX = "lane:"


@dataclass(frozen=True)
class LaneSpec:
    """One bridge lane."""

    name: str
    raw_section: str
    merged: MutableMapping[str, str]


class SectionDict(MutableMapping[str, str]):
    """PyTAK-compatible config mapping: case-insensitive keys like ConfigParser."""

    def __init__(self, section_name: str, data: Mapping[str, str]):
        self._name = section_name
        self._data: Dict[str, str] = {}
        for k, v in data.items():
            self._data[str(k).lower()] = v

    @property
    def name(self) -> str:
        return self._name

    def _norm(self, key: str) -> str:
        return str(key).lower()

    def __getitem__(self, key: str) -> str:
        return self._data[self._norm(key)]

    def __setitem__(self, key: str, value: str) -> None:
        self._data[self._norm(key)] = value

    def __delitem__(self, key: str) -> None:
        del self._data[self._norm(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Match ConfigParser.get: case-insensitive option names."""

        k = self._norm(key)
        return self._data.get(k, default)

    def copy(self) -> "SectionDict":
        return SectionDict(self._name, self._data)


def truthy(val: Optional[str]) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _parse_global(cp: ConfigParser) -> MutableMapping[str, str]:
    if GLOBAL_SECTION not in cp:
        return {}
    base: Dict[str, str] = {}
    for k in cp[GLOBAL_SECTION]:
        base[k] = cp[GLOBAL_SECTION][k]
    return base


def _merge(global_opts: Mapping[str, str], section: Iterable[tuple[str, str]]) -> Dict[str, str]:
    out = dict(global_opts)
    for k, v in section:
        # Skip empty deletes
        out[k.lower()] = v
    return out


def lane_sections(cp: ConfigParser) -> Iterator[tuple[str, str]]:
    """Yield (section_display_name, section_key) for each enabled lane."""

    for name in cp.sections():
        lowered = name.lower()
        if not lowered.startswith(LANE_PREFIX):
            continue
        ln = lowered.split(":", maxsplit=1)[1].strip() or lowered
        if not truthy(cp[name].get("enabled")):
            continue
        yield ln, name


def load_config_parser(path: Path) -> ConfigParser:
    cp = ConfigParser(interpolation=None)
    if not path.is_file():
        raise FileNotFoundError(f"No config file: {path}")

    cp.read(path)
    return cp


def build_lane_specs(cp: ConfigParser) -> tuple[MutableMapping[str, str], tuple[LaneSpec, ...]]:
    """Return globals and enabled lane specs with merged keys."""

    globals_ = _parse_global(cp)
    specs: list[LaneSpec] = []
    for display_name, section_name in lane_sections(cp):
        merged_raw = _merge(globals_, cp[section_name].items())
        # Required keys enforced at startup in bridge/run
        specs.append(LaneSpec(name=display_name, raw_section=section_name, merged=merged_raw))
    return globals_, tuple(specs)


def default_config_path() -> Path:
    return Path(os.environ.get("CHARONTAK_CONFIG", "/etc/charontak.ini"))


def validate_cot_url(url: str, *, lane: str, role: str) -> None:
    """Fail fast with a clear message when PyTAK cannot handle a URL scheme."""

    from urllib.parse import urlparse

    scheme = urlparse(url).scheme.lower()
    if not scheme:
        raise ValueError(f"Lane {lane!r} {role} has empty or invalid URL: {url!r}")

    # PyTAK protocol_factory accepts: tcp, tls/ssl, udp*, log*, file*, tak
    if scheme == "tcp":
        return
    if "udp" in scheme:
        return
    if scheme in ("tls", "ssl", "tak"):
        return
    if "log" in scheme or "file" in scheme:
        return
    if scheme.startswith("tcp+"):
        raise ValueError(
            f"Lane {lane!r} {role} uses {url!r}: PyTAK does not support scheme {scheme!r}. "
            "For TCP listen, PyTAK only supports outbound tcp:// (client). "
            "Disable the lane or point feeders at udp+ro:// mesh instead."
        )
    raise ValueError(
        f"Lane {lane!r} {role} uses unsupported COT_URL scheme {scheme!r} ({url!r}). "
        "See https://pytak.rtfd.io/en/stable/configuration/"
    )


def validate_lanes(lanes: tuple[LaneSpec, ...]) -> None:
    for ln in lanes:
        ing = ln.merged.get("ingress_cot_url") or ln.merged.get("INGRESS_COT_URL")
        egr = ln.merged.get("egress_cot_url") or ln.merged.get("EGRESS_COT_URL")
        if ing:
            validate_cot_url(ing, lane=ln.name, role="ingress")
        if egr:
            validate_cot_url(egr, lane=ln.name, role="egress")


def lane_mode(lane: LaneSpec) -> str:
    raw = (lane.merged.get("mode") or "forward").strip().lower()
    if raw not in ("forward", "reverse", "duplex"):
        raise ValueError(f"Lane {lane.name!r}: invalid mode {raw!r}")
    return raw


def section_for_side(
    lane: LaneSpec,
    *,
    cot_url: str,
    section_suffix: str,
) -> SectionDict:
    """Build a PyTAK-style section dict with COT_URL set."""

    data = dict(lane.merged)
    data["cot_url"] = cot_url
    return SectionDict(f"{lane.name}:{section_suffix}", data)
