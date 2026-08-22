"""Tests for INI parsing and lane specs."""

from pathlib import Path

import pytest

from cotbridge.config import (
    LaneSpec,
    build_lane_specs,
    cot_url_udp_bind_endpoint,
    default_config_path,
    load_config_parser,
    section_for_side,
    truthy,
    validate_cot_url,
    normalize_cot_url,
    validate_lane_udp_bind_conflicts,
    validate_loopback_udp_bind_url,
)


def test_truthy() -> None:
    assert truthy("1") and truthy("true") and truthy(None) is False


def test_build_lane_specs_example(example_ini: Path) -> None:
    cp = load_config_parser(example_ini)
    _, lanes = build_lane_specs(cp, environ={})
    names = [ln.name for ln in lanes]
    assert "mesh-to-server" in names
    assert any(ln.merged.get("mode") == "forward" for ln in lanes)


def test_section_for_side_cot(example_ini: Path) -> None:
    cp = load_config_parser(example_ini)
    _, lanes = build_lane_specs(cp, environ={})
    ln = lanes[0]
    s = section_for_side(ln, cot_url="udp://10.0.0.1:4242", section_suffix="ingress")
    assert s.get("COT_URL") == "udp://10.0.0.1:4242"
    assert s.get("cot_url") == "udp://10.0.0.1:4242"
    s2 = section_for_side(ln, cot_url="udp://:1234", section_suffix="ingress")
    assert s2.get("cot_url") == "udp+ro://0.0.0.0:1234"


def test_transport_environment_is_a_lane_default(example_ini: Path) -> None:
    cp = load_config_parser(example_ini)
    _, lanes = build_lane_specs(
        cp,
        environ={
            "PYTAK_MULTICAST_LOCAL_ADDRS": "10.41.0.1,169.254.2.3",
            "PYTAK_MULTICAST_TTL": "2",
            "COT_URL": "udp+wo://should-not-be-imported:9999",
        },
    )

    assert lanes[0].merged["pytak_multicast_local_addrs"] == (
        "10.41.0.1,169.254.2.3"
    )
    assert lanes[0].merged["pytak_multicast_ttl"] == "2"
    assert "cot_url" not in lanes[0].merged


def test_lane_transport_setting_overrides_environment() -> None:
    from configparser import ConfigParser

    cp = ConfigParser(interpolation=None)
    cp.read_string(
        """
[cotbridge]
PYTAK_MULTICAST_TTL = 1
[lane:mesh]
enabled = true
ingress_cot_url = udp+ro://127.0.0.1:28087
egress_cot_url = udp+wo://239.2.3.1:6969
PYTAK_MULTICAST_LOCAL_ADDRS = 192.0.2.10
"""
    )
    _, lanes = build_lane_specs(
        cp,
        environ={
            "PYTAK_MULTICAST_LOCAL_ADDRS": "169.254.2.3",
            "PYTAK_MULTICAST_TTL": "2",
        },
    )

    assert lanes[0].merged["pytak_multicast_local_addrs"] == "192.0.2.10"
    assert lanes[0].merged["pytak_multicast_ttl"] == "2"


from cotbridge.config import lane_mode


def test_lane_mode_duplex() -> None:
    ln = LaneSpec(
        name="x",
        raw_section="lane:x",
        merged={
            "enabled": "true",
            "ingress_cot_url": "udp://239.2.3.4:6969",
            "egress_cot_url": "tcp://127.0.0.1:8087",
            "mode": "duplex",
        },
    )
    assert lane_mode(ln) == "duplex"


@pytest.fixture()
def example_ini(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parent.parent / "examples" / "cotbridge.ini"
    assert src.is_file()
    dest = tmp_path / "cotbridge.ini"
    dest.write_text(src.read_text(), encoding="utf-8")
    return dest


def test_default_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COTBRIDGE_CONFIG", raising=False)
    assert default_config_path() == Path("/etc/cotbridge.ini")


def test_validate_cot_url_rejects_tcp_ppt() -> None:
    with pytest.raises(ValueError, match="tcp\\+ppt"):
        validate_cot_url("tcp+ppt://127.0.0.1:8087", lane="x", role="ingress")


def test_normalize_cot_url() -> None:
    assert normalize_cot_url("udp://:1234") == "udp+ro://0.0.0.0:1234"
    assert normalize_cot_url("udp://0.0.0.0:1234") == "udp+ro://0.0.0.0:1234"
    assert normalize_cot_url("udp+ro://:1234") == "udp+ro://0.0.0.0:1234"
    assert normalize_cot_url("udp+wo://:1234") == "udp+wo://0.0.0.0:1234"
    assert normalize_cot_url("udp://239.2.3.1:6969") == "udp://239.2.3.1:6969"
    assert normalize_cot_url("tls://example.com:8089") == "tls://example.com:8089"


def test_cot_url_udp_bind_endpoint() -> None:
    assert cot_url_udp_bind_endpoint("udp://:1234") == ("0.0.0.0", 1234)
    assert cot_url_udp_bind_endpoint("udp+ro://239.2.3.1:6969") == ("239.2.3.1", 6969)
    assert cot_url_udp_bind_endpoint("udp+ro://127.0.0.1:18087") == ("127.0.0.1", 18087)
    assert cot_url_udp_bind_endpoint("udp+ro://:18087") == ("0.0.0.0", 18087)
    assert cot_url_udp_bind_endpoint("udp+ro://0.0.0.0:18087") == ("0.0.0.0", 18087)
    assert cot_url_udp_bind_endpoint("udp+wo://239.2.3.1:6969") is None
    assert cot_url_udp_bind_endpoint("tls://example.com:8089") is None


def test_validate_lane_udp_bind_conflicts_normalizes_any_host() -> None:
    lanes = (
        LaneSpec(
            name="a",
            raw_section="lane:a",
            merged={
                "enabled": "true",
                "mode": "forward",
                "ingress_cot_url": "udp+ro://:18087",
                "egress_cot_url": "udp+wo://239.2.3.1:6969",
            },
        ),
        LaneSpec(
            name="b",
            raw_section="lane:b",
            merged={
                "enabled": "true",
                "mode": "forward",
                "ingress_cot_url": "udp+ro://0.0.0.0:18087",
                "egress_cot_url": "udp+wo://239.2.3.1:6969",
            },
        ),
    )
    with pytest.raises(ValueError, match="Conflicting UDP bind"):
        validate_lane_udp_bind_conflicts(lanes)


def test_validate_lane_udp_bind_conflicts() -> None:
    lanes = (
        LaneSpec(
            name="a",
            raw_section="lane:a",
            merged={
                "enabled": "true",
                "mode": "forward",
                "ingress_cot_url": "udp://239.2.3.1:6969",
                "egress_cot_url": "tls://127.0.0.1:8089",
            },
        ),
        LaneSpec(
            name="b",
            raw_section="lane:b",
            merged={
                "enabled": "true",
                "mode": "forward",
                "ingress_cot_url": "udp://239.2.3.1:6969",
                "egress_cot_url": "tak://example/enroll",
            },
        ),
    )
    with pytest.raises(ValueError, match="Conflicting UDP bind"):
        validate_lane_udp_bind_conflicts(lanes)


def test_validate_loopback_udp_bind_url() -> None:
    with pytest.raises(ValueError, match="udp\\+ro://127.0.0.1:18087"):
        validate_loopback_udp_bind_url(
            "udp://127.0.0.1:18087",
            lane="local-to-mesh",
            role="ingress",
        )
    validate_loopback_udp_bind_url(
        "udp+ro://127.0.0.1:18087",
        lane="local-to-mesh",
        role="ingress",
    )
    validate_loopback_udp_bind_url(
        "udp+ro://:18087",
        lane="local-to-mesh",
        role="ingress",
    )
    validate_loopback_udp_bind_url(
        "udp+wo://127.0.0.1:18087",
        lane="local-to-mesh",
        role="egress",
    )
