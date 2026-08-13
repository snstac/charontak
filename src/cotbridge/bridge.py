# Copyright Sensors & Signals LLC https://www.snstac.com/
# SPDX-License-Identifier: Apache-2.0
"""Async bridge: PyTAK RX/TX workers per lane."""

from __future__ import annotations

import asyncio
import errno
import logging
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

import pytak

from cotbridge.config import (
    LaneSpec,
    SectionDict,
    lane_mode,
    record_mode,
    section_for_side,
)
from cotbridge.recorder import TrackRecorder
from cotbridge.log_urls import redact_cot_url
from cotbridge.status import BridgeStatus

LOG = logging.getLogger("cotbridge.bridge")


def _scheme(url: str) -> str:
    return urlparse(url).scheme.lower()


def _is_udp_family(url: str) -> bool:
    return _scheme(url).startswith("udp")


def _queue_size(merged: dict) -> int:
    mi = int(
        merged.get("max_in_queue")
        or merged.get("MAX_IN_QUEUE")
        or pytak.DEFAULT_MAX_IN_QUEUE
    )
    mo = int(
        merged.get("max_out_queue")
        or merged.get("MAX_OUT_QUEUE")
        or pytak.DEFAULT_MAX_OUT_QUEUE
    )
    return max(mi, mo)


def _connect_retry_sleep(merged: dict, attempt: int) -> float:
    base = float(
        merged.get("connect_retry_sleep")
        or merged.get("CONNECT_RETRY_SLEEP")
        or pytak.DEFAULT_SLEEP
    )
    cap = float(
        merged.get("connect_retry_max_sleep")
        or merged.get("CONNECT_RETRY_MAX_SLEEP")
        or pytak.DEFAULT_BACKOFF
    )
    return min(base * (2 ** min(attempt, 6)), cap)


def _connect_error_hint(url: str, exc: OSError) -> str:
    if exc.errno == errno.ECONNREFUSED and _scheme(url).startswith("tcp"):
        return (
            f" Nothing is listening on {redact_cot_url(url)} — start a local CoT TCP "
            "listener on that port, use udp+ro://:PORT for UDP senders, fix the port, "
            "or disable this lane if feeders publish directly to mesh "
            "(udp+wo://239.2.3.1:6969)."
        )
    if exc.errno == errno.EADDRINUSE:
        return " Address already in use (duplicate lane UDP URL or another process?)."
    return ""


def _is_retryable_connect_error(exc: OSError) -> bool:
    return exc.errno in (
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    )


async def _protocol_factory_with_retry(
    cfg: SectionDict,
    *,
    label: str,
    side: str,
    url: str,
    merged: dict,
    on_state: Optional[Any] = None,
) -> tuple[Any, Any]:
    """Connect with backoff; bind conflicts fail immediately."""

    attempt = 0
    while True:
        if on_state:
            on_state("connecting" if attempt == 0 else "retrying")
        try:
            result = await pytak.protocol_factory(cfg)
            if on_state:
                on_state("connected")
            return result
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise OSError(
                    exc.errno,
                    f"{label} {side} bind failed for {redact_cot_url(url)}:"
                    f"{_connect_error_hint(url, exc)}",
                ) from exc
            if not _is_retryable_connect_error(exc):
                raise
            delay = _connect_retry_sleep(merged, attempt)
            attempt += 1
            LOG.warning(
                "%s %s connect failed (%s); retry in %.0fs.%s",
                label,
                side,
                exc,
                delay,
                _connect_error_hint(url, exc),
            )
            if on_state:
                on_state("retrying", str(exc))
            await asyncio.sleep(delay)


def _require_urls(lane: LaneSpec) -> Tuple[str, str]:
    m = lane.merged
    ing = m.get("ingress_cot_url") or m.get("INGRESS_COT_URL")
    egr = m.get("egress_cot_url") or m.get("EGRESS_COT_URL")
    if not ing or not egr:
        raise ValueError(
            f"Lane {lane.name!r} needs ingress_cot_url and egress_cot_url in [{lane.raw_section}]"
        )
    return ing, egr


def _lane_label(lane: LaneSpec) -> str:
    return f"[{lane.raw_section}]"


def log_lane_plan(lanes: Tuple[LaneSpec, ...]) -> None:
    """Log each enabled lane's planned ingress → egress before connections open."""

    for lane in lanes:
        ing_url, egr_url = _require_urls(lane)
        mode = lane_mode(lane)
        LOG.info(
            "%s plan %s: %s -> %s",
            _lane_label(lane),
            mode,
            redact_cot_url(ing_url),
            redact_cot_url(egr_url),
        )


async def _close_udp_writer(writer: Any) -> None:
    if writer is None:
        return
    close = getattr(writer, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:  # noqa: BLE001
        LOG.debug("close writer: %s", exc)


async def _discard_reader_queue(cfg: SectionDict, reader: Any, label: str) -> None:
    """Drain server/client -> CoT by running RXWorker into a sink queue."""

    q: asyncio.Queue = asyncio.Queue()
    rx = pytak.RXWorker(q, cfg, reader)

    async def sink() -> None:
        while True:
            await q.get()

    await asyncio.gather(rx.run(), sink())


async def _record_pump(
    recorder: "TrackRecorder",
    src: asyncio.Queue,
    dst: Optional[asyncio.Queue],
    label: str,
) -> None:
    """Record everything passing through, and forward it unless recording only.

    The recorder is deliberately downstream of nothing: offer() never raises
    and never blocks on I/O, so a recording fault cannot stall or break the
    lane it is observing. Forwarding happens whether or not the record
    succeeded.
    """
    while True:
        data = await src.get()
        try:
            recorder.offer(data)
        except Exception as exc:  # noqa: BLE001
            # TrackRecorder.offer() already swallows its own faults, so this is
            # defence in depth against a recorder object that does not. Moving
            # CoT is the job; an event must never be lost because recording it
            # went wrong.
            LOG.warning("%s recorder raised, continuing: %s", label, exc)
        if dst is not None:
            await dst.put(data)


async def _record_flusher(recorder: "TrackRecorder", interval: float) -> None:
    """Flush on a timer so an idle lane still commits what it buffered.

    Without this, the last events before a quiet spell would sit in RAM
    indefinitely and be lost on restart.
    """
    while True:
        await asyncio.sleep(max(interval, 1.0))
        recorder.flush()


async def _run_lane_session(lane: LaneSpec, status: BridgeStatus) -> None:
    """Run one connected lane session until a worker stops."""

    ing_url, egr_url = _require_urls(lane)
    mode = lane_mode(lane)
    rec_mode = record_mode(lane)
    merged = dict(lane.merged)
    qsz = _queue_size(merged)
    label = _lane_label(lane)
    status.add_lane(lane.name, ing_url, egr_url, mode)

    if rec_mode == "only":
        # EMCON: record locally and emit nothing. Egress is never connected,
        # because opening a socket to a TAK server already announces that this
        # box exists -- which is the thing "only" is for avoiding.
        LOG.info(
            "%s setup %s: %s -> RECORD ONLY (no egress)",
            label,
            mode,
            redact_cot_url(ing_url),
        )
    else:
        LOG.info(
            "%s setup %s: %s -> %s%s",
            label,
            mode,
            redact_cot_url(ing_url),
            redact_cot_url(egr_url),
            " (+record)" if rec_mode == "on" else "",
        )

    ing = section_for_side(lane, cot_url=ing_url, section_suffix="ingress")
    egr = section_for_side(lane, cot_url=egr_url, section_suffix="egress")

    r_ing = w_ing = r_egr = w_egr = None
    r_ing, w_ing = await _protocol_factory_with_retry(
        ing,
        label=label,
        side="ingress",
        url=ing_url,
        merged=merged,
        on_state=lambda state, detail="": status.connection(
            lane.name, "ingress", state, detail
        ),
    )
    LOG.info("%s ingress connected", label)

    if rec_mode != "only":
        try:
            r_egr, w_egr = await _protocol_factory_with_retry(
                egr,
                label=label,
                side="egress",
                url=egr_url,
                merged=merged,
                on_state=lambda state, detail="": status.connection(
                    lane.name, "output", state, detail
                ),
            )
        except OSError:
            await _close_udp_writer(w_ing)
            raise
        LOG.info("%s egress connected", label)
    else:
        status.connection(lane.name, "output", "not_applicable")

    recorder: Optional[TrackRecorder] = None
    if rec_mode in ("on", "only"):
        recorder = TrackRecorder(merged)
        LOG.info(
            "%s recording to %s (budget %s MiB)",
            label,
            recorder.config.directory,
            recorder.config.max_bytes // 1024**2,
        )

    tasks: List[asyncio.Task] = []

    try:
        if rec_mode == "only":
            # Ingress -> recorder, full stop. No egress socket exists.
            relay = status.queue(qsz, tx=False)
            tasks.append(
                asyncio.create_task(
                    pytak.RXWorker(relay, ing, r_ing).run(),
                    name=f"{lane.name}-rx-ingress",
                )
            )
            tasks.append(
                asyncio.create_task(
                    _record_pump(recorder, relay, None, label),
                    name=f"{lane.name}-record",
                )
            )
            tasks.append(
                asyncio.create_task(
                    _record_flusher(recorder, recorder.config.flush_seconds),
                    name=f"{lane.name}-record-flush",
                )
            )

        elif mode == "forward":
            relay: asyncio.Queue = status.queue(qsz, tx=recorder is None)
            tasks.append(
                asyncio.create_task(
                    pytak.RXWorker(relay, ing, r_ing).run(),
                    name=f"{lane.name}-rx-ingress",
                )
            )
            if recorder is not None:
                # Tee between RX and TX: record, then forward unchanged.
                egress_q: asyncio.Queue = status.queue(qsz, rx=False)
                tasks.append(
                    asyncio.create_task(
                        _record_pump(recorder, relay, egress_q, label),
                        name=f"{lane.name}-record",
                    )
                )
                tasks.append(
                    asyncio.create_task(
                        _record_flusher(recorder, recorder.config.flush_seconds),
                        name=f"{lane.name}-record-flush",
                    )
                )
                relay = egress_q
            tasks.append(
                asyncio.create_task(
                    pytak.TXWorker(relay, egr, w_egr).run(),
                    name=f"{lane.name}-tx-egress",
                )
            )
            # A write-only transport (notably udp+wo://) has no reader.  Do not
            # create an RXWorker for None: RXWorker.run_once() returns
            # immediately in that case, so its run loop spins at 100% CPU.
            if r_egr is not None:
                tasks.append(
                    asyncio.create_task(
                        _discard_reader_queue(egr, r_egr, f"{lane.name}-drain-egress"),
                        name=f"{lane.name}-drain-egress",
                    )
                )
            if _is_udp_family(ing_url):
                await _close_udp_writer(w_ing)

        elif mode == "reverse":
            relay = status.queue(qsz)
            tasks.append(
                asyncio.create_task(
                    pytak.RXWorker(relay, egr, r_egr).run(),
                    name=f"{lane.name}-rx-egress",
                )
            )
            tasks.append(
                asyncio.create_task(
                    pytak.TXWorker(relay, ing, w_ing).run(),
                    name=f"{lane.name}-tx-ingress",
                )
            )
            if r_ing is not None:
                tasks.append(
                    asyncio.create_task(
                        _discard_reader_queue(ing, r_ing, f"{lane.name}-drain-ingress"),
                        name=f"{lane.name}-drain-ingress",
                    )
                )
            if _is_udp_family(egr_url):
                await _close_udp_writer(w_egr)

        elif mode == "duplex":
            fwd = status.queue(qsz)
            rev = status.queue(qsz)
            tasks.append(
                asyncio.create_task(
                    pytak.RXWorker(fwd, ing, r_ing).run(),
                    name=f"{lane.name}-rx-ingress-fwd",
                )
            )
            tasks.append(
                asyncio.create_task(
                    pytak.TXWorker(fwd, egr, w_egr).run(),
                    name=f"{lane.name}-tx-egress-fwd",
                )
            )
            tasks.append(
                asyncio.create_task(
                    pytak.RXWorker(rev, egr, r_egr).run(),
                    name=f"{lane.name}-rx-egress-rev",
                )
            )
            tasks.append(
                asyncio.create_task(
                    pytak.TXWorker(rev, ing, w_ing).run(),
                    name=f"{lane.name}-tx-ingress-rev",
                )
            )

        LOG.info(
            "%s active (%s, queue=%s) %s -> %s",
            label,
            mode,
            qsz,
            redact_cot_url(ing_url),
            redact_cot_url(egr_url),
        )
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception()
            if error is not None:
                raise error
        raise ConnectionAbortedError(f"{label} worker exited unexpectedly")

    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if recorder is not None:
            recorder.close()  # commit the buffer rather than losing it
        raise
    except Exception:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if recorder is not None:
            recorder.close()
        status.fault(lane.name, "lane worker exited")
        raise
    finally:
        await _close_udp_writer(w_ing)
        await _close_udp_writer(w_egr)


async def run_lane(lane: LaneSpec, status: BridgeStatus) -> None:
    """Keep a lane alive across long server outages and clean disconnects."""

    attempt = 0
    while True:
        try:
            await _run_lane_session(lane, status)
            attempt = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - supervision boundary
            merged = dict(lane.merged)
            delay = _connect_retry_sleep(merged, attempt)
            attempt += 1
            LOG.warning(
                "%s session stopped (%s); rebuilding lane in %.0fs",
                _lane_label(lane),
                exc,
                delay,
            )
            if lane.name in status.lanes:
                status.connection(lane.name, "output", "retrying", str(exc))
            await asyncio.sleep(delay)


async def run_all(
    lanes: Tuple[LaneSpec, ...], status: Optional[BridgeStatus] = None
) -> None:
    """Run all lanes concurrently."""

    status = status or BridgeStatus()
    heartbeat = asyncio.create_task(status.heartbeat(), name="status-heartbeat")

    if not lanes:
        LOG.warning(
            "No enabled lanes; idle until shutdown (enable a [lane:*] section)."
        )
        try:
            await asyncio.Event().wait()
        finally:
            heartbeat.cancel()
        return

    LOG.info("Starting %s lane(s)", len(lanes))
    log_lane_plan(lanes)

    lane_tasks = [
        asyncio.create_task(run_lane(ln, status), name=f"lane-{ln.name}")
        for ln in lanes
    ]
    try:
        await asyncio.gather(*lane_tasks)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
