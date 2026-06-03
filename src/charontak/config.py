"""Load INI configuration for Charontak."""

from __future__ import annotations

import errno
import os
import socket
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


def _parse_udp_scheme(scheme: str) -> tuple[bool, bool]:
    """Mirror pytak.parse_cot_scheme modifiers for udp* URLs."""

    scheme = scheme.lower()
    write_only = "+wo" in scheme
    read_only = "+ro" in scheme
    return write_only, read_only


def normalize_udp_bind_host(host: str) -> str:
    """Normalize UDP bind addresses for conflict checks and preflight."""

    if not host or host in ("0.0.0.0", "*"):
        return "0.0.0.0"
    return host.lower()


def _udp_url_host_port(url: str) -> tuple[str | None, int | None]:
    from urllib.parse import urlparse

    parsed = urlparse(url.strip())
    port = parsed.port
    host = parsed.hostname
    if host is None and port is not None and parsed.netloc.startswith(":"):
        host = "0.0.0.0"
    return host, port


def normalize_cot_url(url: str) -> str:
    """Normalize UDP CoT URLs for PyTAK.

    ``udp://:PORT`` and ``udp://0.0.0.0:PORT`` mean listen on all interfaces.
    Plain ``udp://`` on 0.0.0.0 is upgraded to ``udp+ro://`` (receive-only bind).
    """

    from urllib.parse import urlparse, urlunparse

    raw = url.strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if not scheme or "udp" not in scheme:
        return raw

    host, port = _udp_url_host_port(raw)
    if host is None or port is None:
        return raw

    write_only, read_only = _parse_udp_scheme(scheme)
    out_scheme = scheme
    if not write_only and not read_only and normalize_udp_bind_host(host) == "0.0.0.0":
        out_scheme = "udp+ro"

    return urlunparse(parsed._replace(scheme=out_scheme, netloc=f"{host}:{port}"))


def cot_url_udp_bind_endpoint(url: str) -> tuple[str, int] | None:
    """Return (host, port) when PyTAK create_udp_client would bind a reader."""

    from urllib.parse import urlparse

    parsed = urlparse(normalize_cot_url(url))
    scheme = parsed.scheme.lower()
    if "udp" not in scheme:
        return None

    write_only, _read_only = _parse_udp_scheme(scheme)
    if write_only:
        return None

    port = parsed.port
    if port is None:
        return None

    host = parsed.hostname
    if host is None:
        return None

    return normalize_udp_bind_host(host), port


def lane_udp_bind_endpoints(lane: LaneSpec) -> tuple[tuple[str, tuple[str, int]], ...]:
    """UDP (host, port) binds this lane would open, tagged by side name."""

    mode = lane_mode(lane)
    ing = lane.merged.get("ingress_cot_url") or lane.merged.get("INGRESS_COT_URL")
    egr = lane.merged.get("egress_cot_url") or lane.merged.get("EGRESS_COT_URL")
    endpoints: list[tuple[str, tuple[str, int]]] = []

    if mode in ("forward", "duplex") and ing:
        ep = cot_url_udp_bind_endpoint(ing)
        if ep:
            endpoints.append(("ingress", ep))
    if mode in ("reverse", "duplex") and egr:
        ep = cot_url_udp_bind_endpoint(egr)
        if ep:
            endpoints.append(("egress", ep))
    return tuple(endpoints)


def validate_lane_udp_bind_conflicts(lanes: tuple[LaneSpec, ...]) -> None:
    """Reject configs where multiple lanes bind the same UDP endpoint."""

    seen: dict[tuple[str, int], list[str]] = {}
    for ln in lanes:
        for side, endpoint in lane_udp_bind_endpoints(ln):
            seen.setdefault(endpoint, []).append(f"{ln.name} ({side})")

    conflicts = {endpoint: users for endpoint, users in seen.items() if len(users) > 1}
    if not conflicts:
        return

    parts: list[str] = []
    for (host, port), users in sorted(conflicts.items()):
        parts.append(f"{host}:{port} ({', '.join(users)})")
    raise ValueError(
        "Conflicting UDP bind endpoints across enabled lanes: "
        + "; ".join(parts)
        + ". Each lane needs a distinct UDP ingress/egress bind, or disable extra lanes."
    )


def validate_loopback_udp_bind_url(url: str, *, lane: str, role: str) -> None:
    """Reject bidirectional udp:// on loopback; udp+ro listen URLs are valid."""

    from urllib.parse import urlparse

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if "udp" not in scheme:
        return

    write_only, read_only = _parse_udp_scheme(scheme)
    if write_only or read_only:
        return

    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return

    port = parsed.port
    if port is None:
        return

    raise ValueError(
        f"Lane {lane!r} {role} uses {url!r}: bidirectional udp:// on loopback is "
        f"ambiguous. To listen for local CoT senders on port {port}, use "
        f"udp+ro://127.0.0.1:{port} or udp+ro://:{port} as {role}_cot_url."
    )


def validate_udp_bind_available(
    endpoint: tuple[str, int], *, lane: str, side: str, url: str
) -> None:
    """Preflight UDP bind so port conflicts fail at startup with a clear message."""

    host, port = endpoint
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        hint = ""
        if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            hint = f" Check: ss -ulnp sport = :{port}"
        raise ValueError(
            f"Lane {lane!r} {side} cannot bind UDP {url!r}: address already in use."
            f"{hint}"
        ) from exc
    finally:
        sock.close()


def validate_lane_udp_binds(lanes: tuple[LaneSpec, ...]) -> None:
    """Validate UDP bind URLs and that each endpoint is available."""

    validate_lane_udp_bind_conflicts(lanes)

    checked: set[tuple[str, int]] = set()
    for ln in lanes:
        for side, endpoint in lane_udp_bind_endpoints(ln):
            if side == "ingress":
                url_key = ln.merged.get("ingress_cot_url") or ln.merged.get("INGRESS_COT_URL") or ""
            else:
                url_key = ln.merged.get("egress_cot_url") or ln.merged.get("EGRESS_COT_URL") or ""
            validate_loopback_udp_bind_url(url_key, lane=ln.name, role=side)
            if endpoint in checked:
                continue
            checked.add(endpoint)
            validate_udp_bind_available(endpoint, lane=ln.name, side=side, url=url_key)


def validate_lanes(lanes: tuple[LaneSpec, ...]) -> None:
    for ln in lanes:
        ing = ln.merged.get("ingress_cot_url") or ln.merged.get("INGRESS_COT_URL")
        egr = ln.merged.get("egress_cot_url") or ln.merged.get("EGRESS_COT_URL")
        if ing:
            validate_cot_url(ing, lane=ln.name, role="ingress")
        if egr:
            validate_cot_url(egr, lane=ln.name, role="egress")
    validate_lane_udp_binds(lanes)


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
    data["cot_url"] = normalize_cot_url(cot_url)
    return SectionDict(f"{lane.name}:{section_suffix}", data)
