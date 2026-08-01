# Copyright Sensors & Signals LLC https://www.snstac.com/
# SPDX-License-Identifier: Apache-2.0
"""On-box recording of CoT tracks, for later and post-hoc analysis.

charontak is the one point every gateway routes through, so recording here
covers ADS-B, AIS, ACARS, APRS, Remote ID and SAPIENT at once, with no
per-gateway work.

Design constraints, in the order they mattered
----------------------------------------------
**charontak must not die because recording failed.** It is the process whose
failure takes the whole box off the map. Every recorder entry point is
wrapped; a broken recorder degrades to not recording, never to dropping CoT.

**But it must not stop QUIETLY.** A recorder that silently stopped three weeks
ago is worse than one that never ran, because the operator believes they have
data. Every stop reason is counted and surfaced in :meth:`stats` for the status
file and Cockpit to display.

**It must not kill the SD card.** These boxes run from flash and bytes-written
is the wear metric. Events are buffered in RAM and flushed once per interval,
never per event, and the flush is zstd-compressed -- roughly 16x less written
than plain JSON lines. Change detection drops the pure repeats that dominate a
quiet scene.

**It must never fill the disk.** A full root filesystem on AryaOS does not just
stop recording: it breaks journald, Cockpit and every gateway at once. The
budget is enforced BEFORE a write, not after, and there is a hard free-space
floor below which recording stops regardless of budget.

**It must survive power loss.** This fleet has documented brownout problems, so
losing power mid-write is a real scenario. Files are append-only sequences of
independently-decodable zstd frames, so an interrupted write costs the last
frame rather than the archive.

Recordings live under /var/lib/charontak/tracks -- deliberately NOT under the
web root. Recorded tracks reveal asset location history, and AryaOS serves
/var/www/html to the LAN and hotspot without authentication.
"""

import glob
import json
import logging
import math
import os
import shutil
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["TrackRecorder", "RecorderConfig", "parse_cot_event"]

LOG = logging.getLogger(__name__)

try:  # pragma: no cover - exercised by availability, not by branch
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None  # type: ignore

DEFAULT_DIR = "/var/lib/charontak/tracks"

# One write per minute rather than per event. At a few hundred events a second
# this is the difference between a card that lasts years and one that does not.
DEFAULT_FLUSH_SECONDS = 60.0

# Bound the RAM buffer independently of time, so a burst cannot grow it without
# limit between flushes.
DEFAULT_MAX_BUFFER = 20000

# Total bytes of recordings to keep. Oldest files are removed past this.
DEFAULT_MAX_BYTES = 4 * 1024**3

DEFAULT_MAX_AGE_DAYS = 30.0

# Recording stops entirely if a flush would leave less than this free, whatever
# the budget says. The budget protects the archive; this protects the system.
DEFAULT_RESERVE_BYTES = 2 * 1024**3

# Change thresholds. A track that has not moved further than this and has no
# changed attributes is a repeat, and repeats dominate a quiet scene.
DEFAULT_MIN_MOVE_M = 10.0
DEFAULT_MIN_ALT_M = 7.6  # ~25 ft

# Even an unchanged track is written this often, so a stationary contact is
# distinguishable in the archive from one that stopped reporting.
DEFAULT_HEARTBEAT_SECONDS = 60.0

EARTH_RADIUS_M = 6371008.8


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def parse_cot_event(raw: bytes) -> Optional[Dict[str, Any]]:
    """Flatten a CoT event into the columns we record.

    Returns None for anything that is not a parseable CoT event, so malformed
    or partial datagrams are dropped rather than recorded as rows of nulls that
    look like real contacts with missing fields.

    The full detail subtree is kept as ``detail_json`` so nothing decoder-
    specific is lost: an ACARS label, a squawk, an MMSI, a Remote ID operator.
    Normalising those into columns would mean a schema change per gateway.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    if root.tag != "event":
        return None

    uid = root.get("uid")
    if not uid:
        # Without a UID there is no track to attribute this to.
        return None

    point = root.find("point")
    detail = root.find("detail")

    def _f(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    callsign = None
    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            callsign = contact.get("callsign")

    row: Dict[str, Any] = {
        "uid": uid,
        "cot_type": root.get("type"),
        "how": root.get("how"),
        "time": root.get("time"),
        "start": root.get("start"),
        "stale": root.get("stale"),
        "callsign": callsign,
        "lat": _f(point.get("lat")) if point is not None else None,
        "lon": _f(point.get("lon")) if point is not None else None,
        "hae": _f(point.get("hae")) if point is not None else None,
        "ce": _f(point.get("ce")) if point is not None else None,
        "le": _f(point.get("le")) if point is not None else None,
    }

    if detail is not None:
        try:
            row["detail_json"] = ET.tostring(detail, encoding="unicode")
        except (TypeError, ValueError):
            row["detail_json"] = None
    else:
        row["detail_json"] = None

    return row


class RecorderConfig:
    """Recorder settings, read from a charontak config section."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        cfg = cfg or {}

        def _get(key: str, default: Any, cast: Any) -> Any:
            value = cfg.get(key)
            if value in (None, ""):
                return default
            try:
                return cast(value)
            except (TypeError, ValueError):
                LOG.warning(
                    "Recorder: %s=%r is not valid, using %r", key, value, default
                )
                return default

        self.directory: str = str(cfg.get("RECORD_DIR") or DEFAULT_DIR)
        self.flush_seconds = _get("RECORD_FLUSH_SECONDS", DEFAULT_FLUSH_SECONDS, float)
        self.max_buffer = _get("RECORD_MAX_BUFFER", DEFAULT_MAX_BUFFER, int)
        self.max_bytes = _get("RECORD_MAX_BYTES", DEFAULT_MAX_BYTES, int)
        self.max_age_days = _get("RECORD_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS, float)
        self.reserve_bytes = _get("RECORD_RESERVE_BYTES", DEFAULT_RESERVE_BYTES, int)
        self.min_move_m = _get("RECORD_MIN_MOVE_M", DEFAULT_MIN_MOVE_M, float)
        self.min_alt_m = _get("RECORD_MIN_ALT_M", DEFAULT_MIN_ALT_M, float)
        self.heartbeat_seconds = _get(
            "RECORD_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS, float
        )
        # Full fidelity is one setting away: record every event as it arrived.
        self.record_all = str(cfg.get("RECORD_ALL", "")).lower() in ("1", "true", "yes")
        # Opt-in encryption at rest. Off by default: a box that cannot read its
        # own recordings after a reboot is a support problem, not security.
        self.encrypt_key_file: Optional[str] = cfg.get("RECORD_ENCRYPT_KEY") or None
        self.compress_level = _get("RECORD_COMPRESS_LEVEL", 10, int)


class TrackRecorder:
    """Buffers CoT events and writes them as compressed, bounded archives."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> None:
        self.config = RecorderConfig(config)
        self._buffer: List[Dict[str, Any]] = []
        self._last_seen: Dict[str, Tuple[float, Optional[float], Optional[float], Optional[float], Optional[str]]] = {}
        self._last_flush = now if now is not None else time.time()

        self.counters: Dict[str, int] = {
            "offered": 0,
            "recorded": 0,
            "dropped_unchanged": 0,
            "dropped_unparseable": 0,
            "flushes": 0,
            "bytes_written": 0,
            "files_pruned": 0,
            "flush_errors": 0,
        }
        # Why recording is not happening, if it is not. Surfaced to the UI:
        # "stopped" must never be indistinguishable from "nothing heard".
        self.halted_reason: Optional[str] = None
        self._logged_halt = False
        self._cipher = self._load_cipher()

    # -- key material -----------------------------------------------------

    def _load_cipher(self):
        """Load the at-rest encryption key, if one is configured."""
        path = self.config.encrypt_key_file
        if not path:
            return None
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            with open(path, "rb") as handle:
                key = handle.read().strip()
            # Accept raw 32-byte keys or hex.
            if len(key) == 64:
                key = bytes.fromhex(key.decode("ascii"))
            if len(key) not in (16, 24, 32):
                raise ValueError(f"key must be 16/24/32 bytes, got {len(key)}")
            return AESGCM(key)
        except Exception as exc:  # noqa: BLE001
            # Refuse to silently write plaintext when encryption was ASKED for.
            # Falling back would produce exactly the file the operator believes
            # is encrypted.
            self._halt(f"encryption key unusable ({exc}); recording disabled")
            return None

    # -- intake -----------------------------------------------------------

    def offer(self, raw: bytes, now: Optional[float] = None) -> bool:
        """Consider one CoT event for recording. Returns True if buffered.

        Never raises: a recorder fault must not propagate into the CoT path.
        """
        if self.halted_reason:
            return False
        now = now if now is not None else time.time()
        try:
            self.counters["offered"] += 1
            row = parse_cot_event(raw)
            if row is None:
                self.counters["dropped_unparseable"] += 1
                return False

            if not self.config.record_all and not self._is_interesting(row, now):
                self.counters["dropped_unchanged"] += 1
                return False

            row["recorded_at"] = round(now, 3)
            self._buffer.append(row)
            self.counters["recorded"] += 1
            self._remember(row, now)

            if len(self._buffer) >= self.config.max_buffer:
                self.flush(now=now, force=True)
            elif (now - self._last_flush) >= self.config.flush_seconds:
                self.flush(now=now)
            return True
        except Exception as exc:  # noqa: BLE001 -- see docstring
            LOG.warning("Recorder: dropping event after error: %s", exc)
            return False

    def _remember(self, row: Dict[str, Any], now: float) -> None:
        self._last_seen[row["uid"]] = (
            now,
            row.get("lat"),
            row.get("lon"),
            row.get("hae"),
            row.get("detail_json"),
        )

    def _is_interesting(self, row: Dict[str, Any], now: float) -> bool:
        """Has anything actually changed since this track was last recorded?

        A parked vessel re-reporting an identical position every two seconds is
        the common case and is close to pure waste. Dropping those is where the
        volume saving comes from; the heartbeat keeps the track visibly alive.
        """
        previous = self._last_seen.get(row["uid"])
        if previous is None:
            return True  # first sight of this track

        last_t, last_lat, last_lon, last_hae, last_detail = previous

        if (now - last_t) >= self.config.heartbeat_seconds:
            return True

        lat, lon = row.get("lat"), row.get("lon")
        if None not in (lat, lon, last_lat, last_lon):
            if _haversine_m(last_lat, last_lon, lat, lon) >= self.config.min_move_m:
                return True
        elif (lat is None) != (last_lat is None):
            return True  # gained or lost a position: a real change

        hae, last_h = row.get("hae"), last_hae
        if None not in (hae, last_h) and abs(hae - last_h) >= self.config.min_alt_m:
            return True

        if row.get("detail_json") != last_detail:
            return True  # squawk, callsign, emergency flag, ACARS label...

        return False

    # -- output -----------------------------------------------------------

    def flush(self, now: Optional[float] = None, force: bool = False) -> int:
        """Write the buffer to a new archive file. Returns bytes written."""
        now = now if now is not None else time.time()
        if not self._buffer:
            self._last_flush = now
            return 0
        if not force and (now - self._last_flush) < self.config.flush_seconds:
            return 0

        try:
            if not self._ensure_space(now):
                # _ensure_space has already halted with a reason. Drop the
                # buffer rather than growing it forever in RAM.
                self._buffer.clear()
                return 0

            payload = "".join(
                json.dumps(row, default=str, separators=(",", ":")) + "\n"
                for row in self._buffer
            ).encode("utf-8")

            blob = self._compress(payload)
            if self._cipher is not None:
                nonce = os.urandom(12)
                blob = nonce + self._cipher.encrypt(nonce, blob, None)

            path = self._archive_path(now)
            os.makedirs(os.path.dirname(path), exist_ok=True)

            # Append-only. Each flush is one independently-decodable frame, so
            # a power cut mid-write costs this frame, not the file.
            with open(path, "ab") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())

            written = len(blob)
            self.counters["bytes_written"] += written
            self.counters["flushes"] += 1
            self._buffer.clear()
            self._last_flush = now
            return written
        except Exception as exc:  # noqa: BLE001
            self.counters["flush_errors"] += 1
            LOG.warning("Recorder: flush failed (%s); buffer dropped", exc)
            self._buffer.clear()
            self._last_flush = now
            return 0

    def _compress(self, payload: bytes) -> bytes:
        if zstandard is None:
            # Not fatal: gzip is stdlib and still far better than raw. Noted
            # once so the ratio difference is explainable later.
            import gzip

            if not self._logged_halt:
                LOG.info("Recorder: python3-zstandard absent, using gzip")
            return gzip.compress(payload, compresslevel=6)
        return zstandard.ZstdCompressor(level=self.config.compress_level).compress(payload)

    def _archive_path(self, now: float) -> str:
        """Hive-style partitioning, so DuckDB can prune by date and hour."""
        stamp = time.gmtime(now)
        ext = "jsonl.zst" if zstandard is not None else "jsonl.gz"
        if self._cipher is not None:
            ext += ".enc"
        return os.path.join(
            self.config.directory,
            f"date={time.strftime('%Y-%m-%d', stamp)}",
            f"hour={time.strftime('%H', stamp)}",
            f"events.{ext}",
        )

    # -- bounds -----------------------------------------------------------

    def _archives(self) -> List[str]:
        pattern = os.path.join(self.config.directory, "date=*", "hour=*", "events.*")
        return sorted(glob.glob(pattern))

    def _ensure_space(self, now: float) -> bool:
        """Enforce retention and the free-space floor BEFORE writing.

        Enforcing after the write is how a disk gets full: the write that
        crosses the line is the one that does the damage.
        """
        self._prune_expired(now)
        self._prune_over_budget()

        try:
            usage = shutil.disk_usage(self._existing_ancestor(self.config.directory))
        except OSError as exc:
            self._halt(f"cannot stat filesystem ({exc})")
            return False

        if usage.free <= self.config.reserve_bytes:
            # Try harder before giving up: drop the oldest archives entirely.
            self._prune_over_budget(target_bytes=self.config.max_bytes // 2)
            try:
                usage = shutil.disk_usage(self._existing_ancestor(self.config.directory))
            except OSError:
                pass

        if usage.free <= self.config.reserve_bytes:
            self._halt(
                f"only {usage.free // 1024**2} MiB free, below the "
                f"{self.config.reserve_bytes // 1024**2} MiB reserve; recording stopped "
                "so the system keeps working"
            )
            return False

        if self.halted_reason:
            # Space came back.
            LOG.info("Recorder: resuming, space recovered")
            self.halted_reason = None
            self._logged_halt = False
        return True

    @staticmethod
    def _existing_ancestor(path: str) -> str:
        while path and not os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        return path or "/"

    def _prune_expired(self, now: float) -> None:
        if self.config.max_age_days <= 0:
            return
        cutoff = now - self.config.max_age_days * 86400
        for path in self._archives():
            try:
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
                    self.counters["files_pruned"] += 1
            except OSError:
                continue
        self._prune_empty_dirs()

    def _prune_over_budget(self, target_bytes: Optional[int] = None) -> None:
        budget = target_bytes if target_bytes is not None else self.config.max_bytes
        if budget <= 0:
            return
        archives = self._archives()
        sizes = []
        total = 0
        for path in archives:
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            sizes.append((path, size))
            total += size
        # Oldest first: _archives() sorts by date=/hour= path, which is
        # chronological because the stamps are zero-padded and UTC.
        for path, size in sizes:
            if total <= budget:
                break
            try:
                os.unlink(path)
                total -= size
                self.counters["files_pruned"] += 1
            except OSError:
                continue
        self._prune_empty_dirs()

    def _prune_empty_dirs(self) -> None:
        for pattern in ("date=*/hour=*", "date=*"):
            for path in glob.glob(os.path.join(self.config.directory, pattern)):
                try:
                    os.rmdir(path)
                except OSError:
                    continue  # not empty, or gone

    def _halt(self, reason: str) -> None:
        self.halted_reason = reason
        if not self._logged_halt:
            self._logged_halt = True
            LOG.warning("Recorder: %s", reason)

    # -- reporting --------------------------------------------------------

    def disk_usage(self) -> int:
        total = 0
        for path in self._archives():
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
        return total

    def stats(self) -> Dict[str, Any]:
        """What the status file and Cockpit show."""
        return {
            "recording": self.halted_reason is None,
            "halted_reason": self.halted_reason,
            "directory": self.config.directory,
            "buffered": len(self._buffer),
            "tracked_uids": len(self._last_seen),
            "bytes_on_disk": self.disk_usage(),
            "budget_bytes": self.config.max_bytes,
            "encrypted": self._cipher is not None,
            "counters": dict(self.counters),
        }

    def close(self) -> None:
        """Flush whatever is buffered. Safe to call more than once."""
        try:
            self.flush(force=True)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Recorder: final flush failed: %s", exc)
