"""Tests for bridge relay with mocked PyTAK protocol_factory."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from cotbridge.bridge import run_all, run_lane
from cotbridge.config import LaneSpec
from cotbridge.status import BridgeStatus


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
    from cotbridge.config import lane_mode

    with pytest.raises(ValueError, match="invalid mode"):
        lane_mode(ln)


@pytest.mark.asyncio
async def test_run_lane_logs_setup_before_connect(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="cotbridge.bridge")

    async def fake_pf(cfg):
        return BlockReader(), MagicMock()

    monkeypatch.setattr("cotbridge.bridge.pytak.protocol_factory", fake_pf)

    ln = LaneSpec(
        name="t",
        raw_section="lane:t",
        merged={
            "enabled": "true",
            "mode": "forward",
            "ingress_cot_url": "udp://239.2.3.4:6969",
            "egress_cot_url": "tcp://127.0.0.1:18087",
        },
    )

    task = asyncio.create_task(
        run_lane(ln, BridgeStatus(path=str(tmp_path / "status.json")))
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    messages = [r.message for r in caplog.records if r.name == "cotbridge.bridge"]
    assert any("setup forward" in m for m in messages)
    assert any("ingress connected" in m for m in messages)


@pytest.mark.asyncio
async def test_forward_one_cot_via_mocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
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

    monkeypatch.setattr("cotbridge.bridge.pytak.protocol_factory", fake_pf)

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

    task = asyncio.create_task(
        run_lane(ln, BridgeStatus(path=str(tmp_path / "status.json")))
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sent, "egress writer should receive relayed CoT bytes"


@pytest.mark.asyncio
async def test_forward_write_only_egress_does_not_start_reader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A udp+wo egress has no reader; trying to drain it busy-loops PyTAK."""

    discarded: list[object] = []

    class EgWriter:
        async def send(self, data: bytes) -> None:  # noqa: ARG002
            return None

    async def fake_pf(cfg):
        url = str(cfg.get("COT_URL") or cfg.get("cot_url") or "")
        if url.startswith("udp+ro://"):
            return BlockReader(), MagicMock()
        if url.startswith("udp+wo://"):
            return None, EgWriter()
        raise AssertionError(url)

    async def fake_discard(cfg, reader, label):  # noqa: ARG001
        discarded.append(reader)
        await asyncio.Event().wait()

    monkeypatch.setattr("cotbridge.bridge.pytak.protocol_factory", fake_pf)
    monkeypatch.setattr("cotbridge.bridge._discard_reader_queue", fake_discard)

    lane = LaneSpec(
        name="mesh",
        raw_section="lane:mesh",
        merged={
            "enabled": "true",
            "mode": "forward",
            "ingress_cot_url": "udp+ro://127.0.0.1:28087",
            "egress_cot_url": "udp+wo://239.2.3.1:6969",
        },
    )

    task = asyncio.create_task(
        run_lane(lane, BridgeStatus(path=str(tmp_path / "status.json")))
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert discarded == []


@pytest.mark.asyncio
async def test_protocol_factory_with_retry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import errno
    import logging

    from cotbridge.bridge import _protocol_factory_with_retry
    from cotbridge.config import SectionDict

    caplog.set_level(logging.WARNING, logger="cotbridge.bridge")
    attempts = {"n": 0}

    async def fake_pf(cfg):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError(errno.ECONNREFUSED, "Connect call failed")
        return BlockReader(), MagicMock()

    monkeypatch.setattr("cotbridge.bridge.pytak.protocol_factory", fake_pf)

    async def fast_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("cotbridge.bridge.asyncio.sleep", fast_sleep)

    cfg = SectionDict("t:ingress", {"cot_url": "tcp://127.0.0.1:18087"})
    await _protocol_factory_with_retry(
        cfg,
        label="[lane:t]",
        side="ingress",
        url="tcp://127.0.0.1:18087",
        merged={},
    )

    assert attempts["n"] == 2
    assert any("Nothing is listening" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_all_idle_cancel(tmp_path) -> None:
    status = BridgeStatus(path=str(tmp_path / "status.json"))
    t = asyncio.create_task(run_all((), status=status))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


@pytest.mark.asyncio
async def test_run_lane_rebuilds_session_after_disconnect(
    monkeypatch, tmp_path
) -> None:
    """A receive worker exit must reconnect, not leave a half-alive service."""

    attempts = 0
    reconnected = asyncio.Event()

    async def fake_session(lane, status):  # noqa: ARG001
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionAbortedError("server closed websocket")
        reconnected.set()
        await asyncio.Event().wait()

    async def no_delay(delay):  # noqa: ARG001
        return None

    monkeypatch.setattr("cotbridge.bridge._run_lane_session", fake_session)
    monkeypatch.setattr("cotbridge.bridge.asyncio.sleep", no_delay)
    lane = LaneSpec(
        name="site-output",
        raw_section="lane:site-output",
        merged={
            "ingress_cot_url": "udp+ro://127.0.0.1:28087",
            "egress_cot_url": "tls://example:8089",
        },
    )
    status = BridgeStatus(path=str(tmp_path / "status.json"))
    task = asyncio.create_task(run_lane(lane, status))
    await asyncio.wait_for(reconnected.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts == 2
