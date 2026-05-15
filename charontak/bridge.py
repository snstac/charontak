"""Async bridge: PyTAK RX/TX workers per lane."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Tuple
from urllib.parse import urlparse

import pytak

from charontak.config import LaneSpec, SectionDict, lane_mode, section_for_side

LOG = logging.getLogger("charontak.bridge")


def _scheme(url: str) -> str:
    return urlparse(url).scheme.lower()


def _is_udp_family(url: str) -> bool:
    return _scheme(url).startswith("udp")


def _queue_size(merged: dict) -> int:
    mi = int(merged.get("max_in_queue") or merged.get("MAX_IN_QUEUE") or pytak.DEFAULT_MAX_IN_QUEUE)
    mo = int(merged.get("max_out_queue") or merged.get("MAX_OUT_QUEUE") or pytak.DEFAULT_MAX_OUT_QUEUE)
    return max(mi, mo)


def _require_urls(lane: LaneSpec) -> Tuple[str, str]:
    m = lane.merged
    ing = m.get("ingress_cot_url") or m.get("INGRESS_COT_URL")
    egr = m.get("egress_cot_url") or m.get("EGRESS_COT_URL")
    if not ing or not egr:
        raise ValueError(
            f"Lane {lane.name!r} needs ingress_cot_url and egress_cot_url in [{lane.raw_section}]"
        )
    return ing, egr


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


async def run_lane(lane: LaneSpec) -> None:
    """Run one lane until cancelled or failure."""

    ing_url, egr_url = _require_urls(lane)
    mode = lane_mode(lane)
    merged = dict(lane.merged)
    qsz = _queue_size(merged)

    ing = section_for_side(lane, cot_url=ing_url, section_suffix="ingress")
    egr = section_for_side(lane, cot_url=egr_url, section_suffix="egress")

    r_ing, w_ing = await pytak.protocol_factory(ing)
    r_egr, w_egr = await pytak.protocol_factory(egr)

    tasks: List[asyncio.Task] = []

    try:
        if mode == "forward":
            relay: asyncio.Queue = asyncio.Queue(qsz)
            tasks.append(asyncio.create_task(pytak.RXWorker(relay, ing, r_ing).run(), name=f"{lane.name}-rx-ingress"))
            tasks.append(asyncio.create_task(pytak.TXWorker(relay, egr, w_egr).run(), name=f"{lane.name}-tx-egress"))
            tasks.append(
                asyncio.create_task(_discard_reader_queue(egr, r_egr, f"{lane.name}-drain-egress"), name=f"{lane.name}-drain-egress")
            )
            if _is_udp_family(ing_url):
                await _close_udp_writer(w_ing)

        elif mode == "reverse":
            relay = asyncio.Queue(qsz)
            tasks.append(asyncio.create_task(pytak.RXWorker(relay, egr, r_egr).run(), name=f"{lane.name}-rx-egress"))
            tasks.append(asyncio.create_task(pytak.TXWorker(relay, ing, w_ing).run(), name=f"{lane.name}-tx-ingress"))
            tasks.append(
                asyncio.create_task(_discard_reader_queue(ing, r_ing, f"{lane.name}-drain-ingress"), name=f"{lane.name}-drain-ingress")
            )
            if _is_udp_family(egr_url):
                await _close_udp_writer(w_egr)

        elif mode == "duplex":
            fwd = asyncio.Queue(qsz)
            rev = asyncio.Queue(qsz)
            tasks.append(asyncio.create_task(pytak.RXWorker(fwd, ing, r_ing).run(), name=f"{lane.name}-rx-ingress-fwd"))
            tasks.append(asyncio.create_task(pytak.TXWorker(fwd, egr, w_egr).run(), name=f"{lane.name}-tx-egress-fwd"))
            tasks.append(asyncio.create_task(pytak.RXWorker(rev, egr, r_egr).run(), name=f"{lane.name}-rx-egress-rev"))
            tasks.append(asyncio.create_task(pytak.TXWorker(rev, ing, w_ing).run(), name=f"{lane.name}-tx-ingress-rev"))

        LOG.info(
            "Lane %r started (%s) %s -> %s",
            lane.name,
            mode,
            ing_url,
            egr_url,
        )
        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except Exception:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_all(lanes: Tuple[LaneSpec, ...]) -> None:
    """Run all lanes concurrently."""

    if not lanes:
        LOG.warning("No enabled lanes; idle until shutdown (enable a [lane:*] section).")
        await asyncio.Event().wait()
        return

    lane_tasks = [asyncio.create_task(run_lane(ln), name=f"lane-{ln.name}") for ln in lanes]
    await asyncio.gather(*lane_tasks)
