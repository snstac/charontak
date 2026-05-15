"""Tests for INI parsing and lane specs."""

from pathlib import Path

import pytest

from charontak.config import (
    LaneSpec,
    build_lane_specs,
    default_config_path,
    load_config_parser,
    section_for_side,
    truthy,
)


def test_truthy() -> None:
    assert truthy("1") and truthy("true") and truthy(None) is False


def test_build_lane_specs_example(example_ini: Path) -> None:
    cp = load_config_parser(example_ini)
    _, lanes = build_lane_specs(cp)
    names = [ln.name for ln in lanes]
    assert "mesh-to-server" in names
    assert any(ln.merged.get("mode") == "forward" for ln in lanes)


def test_section_for_side_cot(example_ini: Path) -> None:
    cp = load_config_parser(example_ini)
    _, lanes = build_lane_specs(cp)
    ln = lanes[0]
    s = section_for_side(ln, cot_url="udp://10.0.0.1:4242", section_suffix="ingress")
    assert s.get("COT_URL") == "udp://10.0.0.1:4242"
    assert s.get("cot_url") == "udp://10.0.0.1:4242"


from charontak.config import lane_mode


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
    src = Path(__file__).resolve().parent.parent / "examples" / "charontak.ini"
    assert src.is_file()
    dest = tmp_path / "charontak.ini"
    dest.write_text(src.read_text(), encoding="utf-8")
    return dest


def test_default_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHARONTAK_CONFIG", raising=False)
    assert default_config_path() == Path("/etc/charontak.ini")
