# Copyright Sensors & Signals LLC https://www.snstac.com/
# SPDX-License-Identifier: Apache-2.0
"""One bounded runtime-health document for all COTBridge lanes."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Optional

import pytak

from cotbridge import __version__
from cotbridge.log_urls import redact_cot_url


class CountingQueue(asyncio.Queue):
    """Queue which reports traffic without changing PyTAK worker behavior."""

    def __init__(
        self,
        maxsize: int,
        *,
        on_put: Optional[Callable[[], None]] = None,
        on_get: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(maxsize)
        self._on_put = on_put
        self._on_get = on_get

    async def put(self, item: Any) -> None:
        await super().put(item)
        if self._on_put:
            self._on_put()

    async def get(self) -> Any:
        item = await super().get()
        if self._on_get:
            self._on_get()
        return item


class BridgeStatus:
    """Aggregate lane state into the common PyTAK health contract."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.writer = pytak.StatusWriter("cotbridge", path=path, version=__version__)
        self.lanes: Dict[str, Dict[str, Any]] = {}
        self.last_input: Optional[float] = None
        self.last_output: Optional[float] = None
        self.input_total = 0
        self.output_total = 0

    def add_lane(self, name: str, ingress: str, egress: str, mode: str) -> None:
        self.lanes[name] = {
            "mode": mode,
            "ingress": {"state": "connecting", "url": redact_cot_url(ingress)},
            "output": {"state": "connecting", "url": redact_cot_url(egress)},
        }
        self.refresh()

    def connection(self, name: str, side: str, state: str, detail: str = "") -> None:
        lane = self.lanes[name]
        block = lane[side]
        block["state"] = state
        if detail:
            block["detail"] = detail
        else:
            block.pop("detail", None)
        block["changed"] = round(time.time(), 3)
        self.refresh()
        self.writer.write()

    def fault(self, name: str, detail: str) -> None:
        lane = self.lanes[name]
        lane["health"] = {"state": "fault", "detail": str(detail)}
        self.refresh()
        self.writer.write(force=True)

    def received(self) -> None:
        self.last_input = time.time()
        self.input_total += 1
        self.writer.count("rx")

    def emitted(self) -> None:
        self.last_output = time.time()
        self.output_total += 1
        self.writer.count("tx")

    def queue(self, maxsize: int, *, rx: bool = True, tx: bool = True) -> CountingQueue:
        return CountingQueue(
            maxsize,
            on_put=self.received if rx else None,
            on_get=self.emitted if tx else None,
        )

    def refresh(self) -> None:
        if not self.lanes:
            self.writer.set_health("degraded", "no enabled lanes")
            self.writer.set_output("not_applicable")
            self.writer.set(lanes={})
            return

        states = [lane["output"]["state"] for lane in self.lanes.values()]
        faults = [
            lane
            for lane in self.lanes.values()
            if lane.get("health", {}).get("state") == "fault"
        ]
        retries = any(
            side.get("state") in ("connecting", "retrying", "disconnected")
            for lane in self.lanes.values()
            for side in (lane["ingress"], lane["output"])
        )
        if faults:
            self.writer.set_health("fault", "one or more lanes failed")
        elif retries:
            self.writer.set_health("degraded", "one or more lanes are reconnecting")
        else:
            self.writer.set_health("ok", "all lanes active")

        if any(state == "retrying" for state in states):
            output_state = "retrying"
        elif any(state in ("connecting", "disconnected") for state in states):
            output_state = "connecting"
        elif all(state == "not_applicable" for state in states):
            output_state = "not_applicable"
        else:
            output_state = "connected"

        self.writer.set_input(last_observation=self.last_input, total=self.input_total)
        self.writer.set_output(
            output_state,
            last_success=self.last_output,
            total=self.output_total,
        )
        self.writer.set(lanes=self.lanes)

    async def heartbeat(self) -> None:
        while True:
            self.refresh()
            self.writer.write()
            await asyncio.sleep(1.0)
