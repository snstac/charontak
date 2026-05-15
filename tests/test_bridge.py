"""Tests for bridge relay with mocked PyTAK protocol_factory."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from charontak.bridge import run_all, run_lane
from charontak.config import LaneSpec


class IngressReader:
    def __init__(self) -> None:
        self._n = 0

    async def readuntil(self, delimiter: bytes) -> bytes:  # noqa: ARG002
        if self._n == 0:
            self._n += 1
            # Minimal CoT terminator for RXWorker.readcot()
            return b'<?xml version="1.0"?><event version="2.0" uid="test" type="t-x"></event>'
        await asyncio.sleep(3600.0)


class BlockReader:
    async def readuntil(self, delimiter: bytes) -> bytes:  # noqa: ARG002
        await asyncio.sleep(3600.0)


def test_lane_mode_bad() -> None:
    ln = LaneSpec(
        name="bad",
        raw_section="lane:bad",
        merged={
            "enabled": "true",
            "ingress_cot_url": "udp://239.2.3.4:6969",
            "egress_cot_url": "tcp://127.0.0.1:8087",
            "mode": "sideways",
        },
    )
    from charontak.config import lane_mode

    with pytest.raises(ValueError, match="invalid mode"):
        lane_mode(ln)


@pytest.mark.asyncio
async def test_forward_one_cot_via_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[bytes] = []

    mock_w = MagicMock()
    mock_w.close = MagicMock()

    class EgWriter:
        async def send(self, data: bytes) -> None:
            sent.append(data)

        def write(self, data: bytes) -> None:
            sent.append(data)

        async def drain(self) -> None:
            return None

    async def fake_pf(cfg):
        u = str(cfg.get("COT_URL") or cfg.get("cot_url") or "")
        if u.startswith("udp://"):
            return IngressReader(), mock_w
        if u.startswith("tcp://"):
            return BlockReader(), EgWriter()
        raise AssertionError(u)

    monkeypatch.setattr("charontak.bridge.pytak.protocol_factory", fake_pf)

    ln = LaneSpec(
        name="t",
        raw_section="lane:t",
        merged={
            "enabled": "true",
            "mode": "forward",
            "ingress_cot_url": "udp://239.2.3.4:6969",
            "egress_cot_url": "tcp://127.0.0.1:18087",
            "max_in_queue": "50",
            "max_out_queue": "50",
        },
    )

    task = asyncio.create_task(run_lane(ln))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sent, "egress writer should receive relayed CoT bytes"


@pytest.mark.asyncio
async def test_run_all_idle_cancel() -> None:
    t = asyncio.create_task(run_all(()))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
