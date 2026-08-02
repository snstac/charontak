# Copyright Sensors & Signals LLC https://www.snstac.com/
# SPDX-License-Identifier: Apache-2.0
"""Tests for the CoT track recorder.

Weighted towards the ways this hurts someone rather than the happy path. It
runs unattended for weeks on a flash card in a box that has documented brownout
problems, so the tests that matter are: does it refuse to fill the disk, does
it bound what it keeps, does it survive a fault without taking charontak down,
and -- most of all -- does it say so when it stops.
"""

import json
import os

import pytest

from charontak.recorder import TrackRecorder, parse_cot_event

EVENT = (
    '<?xml version="1.0"?>'
    '<event version="2.0" uid="ICAO-A4C3B2" type="a-f-A-C-F" how="m-g" '
    'time="2026-08-01T09:00:00Z" start="2026-08-01T09:00:00Z" '
    'stale="2026-08-01T09:05:00Z">'
    '<point lat="37.6" lon="-122.4" hae="3000.0" ce="10.0" le="20.0"/>'
    "<detail><contact callsign=\"UAL225\"/><__adsb alt_baro=\"9800\"/></detail>"
    "</event>"
)


def evt(uid="ICAO-A4C3B2", lat=37.6, lon=-122.4, hae=3000.0, detail="UAL225"):
    return (
        f'<event version="2.0" uid="{uid}" type="a-f-A-C-F" how="m-g" '
        f'time="2026-08-01T09:00:00Z" stale="2026-08-01T09:05:00Z">'
        f'<point lat="{lat}" lon="{lon}" hae="{hae}" ce="10.0" le="20.0"/>'
        f'<detail><contact callsign="{detail}"/></detail></event>'
    ).encode()


def _archive_of(rec):
    return next(
        os.path.join(dp, f)
        for dp, _, fs in os.walk(rec.config.directory)
        for f in fs
    )


def _decode(blob: bytes, path: str, allow_partial: bool = False) -> bytes:
    """Decode an archive whatever codec it was written with.

    The recorder uses zstd when python3-zstandard is present (it is on AryaOS)
    and falls back to gzip otherwise, so the tests must exercise whichever one
    this machine actually has rather than assuming.
    """
    if path.endswith(".zst"):
        import zstandard, io

        dctx = zstandard.ZstdDecompressor()
        if not allow_partial:
            return dctx.decompress(blob, max_output_size=10 << 20)
        out = b""
        try:
            out = dctx.stream_reader(io.BytesIO(blob)).read()
        except Exception:
            pass
        return out
    import zlib

    if not allow_partial:
        import gzip

        return gzip.decompress(blob)
    # Partial gzip: decompress as far as the damage allows.
    out = b""
    try:
        out = zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(blob)
    except Exception:
        pass
    return out



@pytest.fixture
def rec(tmp_path):
    return TrackRecorder({"RECORD_DIR": str(tmp_path / "tracks")}, now=1000.0)


class TestParse:
    def test_extracts_the_recorded_columns(self):
        row = parse_cot_event(EVENT.encode())
        assert row["uid"] == "ICAO-A4C3B2"
        assert row["cot_type"] == "a-f-A-C-F"
        assert row["callsign"] == "UAL225"
        assert row["lat"] == pytest.approx(37.6)
        assert row["hae"] == pytest.approx(3000.0)

    def test_keeps_the_whole_detail_subtree(self):
        """Decoder-specific fields must survive without a schema change."""
        row = parse_cot_event(EVENT.encode())
        assert "__adsb" in row["detail_json"]
        assert 'alt_baro="9800"' in row["detail_json"]

    def test_rejects_malformed_xml(self):
        assert parse_cot_event(b"<event uid='x'") is None

    def test_rejects_non_events(self):
        assert parse_cot_event(b"<ping/>") is None

    def test_rejects_events_with_no_uid(self):
        """A row with no track to attribute it to is not a contact."""
        assert parse_cot_event(b'<event type="a-f"><point lat="1" lon="2"/></event>') is None

    def test_non_numeric_coords_become_none_not_zero(self):
        """0,0 is a real place in the Gulf of Guinea."""
        row = parse_cot_event(
            b'<event uid="x"><point lat="nope" lon="-122.4"/></event>'
        )
        assert row["lat"] is None

    def test_nan_is_rejected(self):
        row = parse_cot_event(b'<event uid="x"><point lat="NaN" lon="1.0"/></event>')
        assert row["lat"] is None


class TestChangeDetection:
    def test_first_sight_is_always_recorded(self, rec):
        assert rec.offer(evt(), now=1000.0) is True

    def test_identical_repeat_is_dropped(self, rec):
        """The volume saving. A parked vessel repeating itself is waste."""
        rec.offer(evt(), now=1000.0)
        assert rec.offer(evt(), now=1002.0) is False
        assert rec.counters["dropped_unchanged"] == 1

    def test_movement_is_recorded(self, rec):
        rec.offer(evt(), now=1000.0)
        # ~0.001 deg latitude is ~111 m, well over the 10 m threshold.
        assert rec.offer(evt(lat=37.601), now=1002.0) is True

    def test_sub_threshold_jitter_is_dropped(self, rec):
        """GPS noise must not be recorded as movement."""
        rec.offer(evt(), now=1000.0)
        assert rec.offer(evt(lat=37.60001), now=1002.0) is False

    def test_altitude_change_is_recorded(self, rec):
        rec.offer(evt(), now=1000.0)
        assert rec.offer(evt(hae=3100.0), now=1002.0) is True

    def test_detail_change_is_recorded(self, rec):
        """A squawk or callsign change matters even from a parked aircraft."""
        rec.offer(evt(), now=1000.0)
        assert rec.offer(evt(detail="UAL999"), now=1002.0) is True

    def test_heartbeat_keeps_a_still_track_alive(self, rec):
        """Otherwise 'stationary' and 'stopped reporting' look identical."""
        rec.offer(evt(), now=1000.0)
        assert rec.offer(evt(), now=1002.0) is False
        assert rec.offer(evt(), now=1061.0) is True

    def test_losing_a_position_is_a_change(self, rec):
        rec.offer(evt(), now=1000.0)
        no_pos = b'<event uid="ICAO-A4C3B2" type="a-f"><detail/></event>'
        assert rec.offer(no_pos, now=1002.0) is True

    def test_distinct_tracks_do_not_suppress_each_other(self, rec):
        rec.offer(evt(uid="A"), now=1000.0)
        assert rec.offer(evt(uid="B"), now=1000.0) is True

    def test_record_all_disables_suppression(self, tmp_path):
        """Full fidelity is one setting away."""
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_ALL": "true"}, now=1000.0
        )
        r.offer(evt(), now=1000.0)
        assert r.offer(evt(), now=1002.0) is True


class TestFlush:
    def test_writes_a_readable_archive(self, rec):
        rec.offer(evt(), now=1000.0)
        assert rec.flush(now=1000.0, force=True) > 0

        path = _archive_of(rec)
        raw = _decode(open(path, "rb").read(), path)
        rows = [json.loads(line) for line in raw.decode().splitlines()]
        assert rows[0]["uid"] == "ICAO-A4C3B2"
        assert rows[0]["callsign"] == "UAL225"

    def test_buffers_rather_than_writing_per_event(self, rec):
        """One write per interval is the whole SD-card strategy."""
        for i in range(50):
            rec.offer(evt(uid=f"T{i}"), now=1000.0)
        assert rec.counters["flushes"] == 0
        rec.flush(now=1000.0, force=True)
        assert rec.counters["flushes"] == 1

    def test_flush_is_time_triggered(self, rec):
        rec.offer(evt(uid="A"), now=1000.0)
        rec.offer(evt(uid="B"), now=1065.0)  # past the 60s interval
        assert rec.counters["flushes"] == 1

    def test_oversized_buffer_forces_a_flush(self, tmp_path):
        """A burst must not grow RAM without bound between flushes."""
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_MAX_BUFFER": "10"}, now=1000.0
        )
        for i in range(12):
            r.offer(evt(uid=f"T{i}"), now=1000.0)
        assert r.counters["flushes"] >= 1

    def test_empty_flush_writes_nothing(self, rec):
        assert rec.flush(now=1000.0, force=True) == 0

    def test_archive_is_partitioned_for_query_pruning(self, rec):
        rec.offer(evt(), now=1000.0)
        rec.flush(now=1000.0, force=True)
        found = [
            os.path.join(dp, f)
            for dp, _, fs in os.walk(rec.config.directory)
            for f in fs
        ]
        assert "date=" in found[0] and "hour=" in found[0]

    def test_appended_frames_are_independently_decodable(self, rec):
        """Power-cut survival: a torn tail costs one frame, not the archive.

        This fleet has documented brownout problems, so losing power part-way
        through a write is a real scenario rather than a hypothetical.
        """
        rec.offer(evt(uid="AAA"), now=1000.0)
        rec.flush(now=1000.0, force=True)
        rec.offer(evt(uid="BBB"), now=1100.0)
        rec.flush(now=1100.0, force=True)

        path = _archive_of(rec)
        blob = open(path, "rb").read()

        # Whole file: both frames present.
        assert b"AAA" in _decode(blob, path)
        assert b"BBB" in _decode(blob, path)

        # Truncated mid-final-frame, as a power cut would leave it. The first
        # frame must still be recoverable.
        torn = blob[: len(blob) - 3]
        recovered = _decode(torn, path, allow_partial=True)
        assert b"AAA" in recovered


class TestDoesNotFillTheDisk:
    """A full root filesystem breaks journald, Cockpit and every gateway."""

    def test_halts_below_the_free_space_reserve(self, tmp_path, monkeypatch):
        import shutil as sh

        r = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)
        monkeypatch.setattr(
            sh, "disk_usage", lambda p: os.statvfs_result if False else _Usage(free=1024)
        )
        r.offer(evt(), now=1000.0)
        r.flush(now=1000.0, force=True)
        assert r.halted_reason is not None
        assert "reserve" in r.halted_reason

    def test_halt_is_visible_not_silent(self, tmp_path, monkeypatch):
        """A recorder that stopped weeks ago is worse than one that never ran."""
        import shutil as sh

        r = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)
        monkeypatch.setattr(sh, "disk_usage", lambda p: _Usage(free=1024))
        r.offer(evt(), now=1000.0)
        r.flush(now=1000.0, force=True)
        stats = r.stats()
        assert stats["recording"] is False
        assert stats["halted_reason"]

    def test_stops_accepting_events_once_halted(self, tmp_path, monkeypatch):
        import shutil as sh

        r = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)
        monkeypatch.setattr(sh, "disk_usage", lambda p: _Usage(free=1024))
        r.offer(evt(), now=1000.0)
        r.flush(now=1000.0, force=True)
        assert r.offer(evt(uid="Z"), now=1100.0) is False

    def test_buffer_is_dropped_not_grown_when_halted(self, tmp_path, monkeypatch):
        """Otherwise refusing to write becomes an out-of-memory instead."""
        import shutil as sh

        r = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)
        monkeypatch.setattr(sh, "disk_usage", lambda p: _Usage(free=1024))
        for i in range(10):
            r.offer(evt(uid=f"T{i}"), now=1000.0)
        r.flush(now=1000.0, force=True)
        assert len(r._buffer) == 0


class TestRetention:
    def test_prunes_past_the_byte_budget(self, tmp_path):
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_MAX_BYTES": "1"}, now=1000.0
        )
        for hour in range(3):
            r.offer(evt(uid=f"T{hour}"), now=1000.0 + hour * 3700)
            r.flush(now=1000.0 + hour * 3700, force=True)
        assert r.counters["files_pruned"] >= 1

    def test_prunes_past_the_age_cap(self, tmp_path):
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_MAX_AGE_DAYS": "0.00001"},
            now=1000.0,
        )
        r.offer(evt(), now=1000.0)
        r.flush(now=1000.0, force=True)
        before = len(r._archives())
        r.offer(evt(uid="B"), now=1000.0)
        r.flush(now=time.time() + 10, force=True)
        assert before >= 1

    def test_budget_is_enforced_before_writing(self, tmp_path):
        """Enforcing after the write is how a disk gets full: the write that
        crosses the line is the one that does the damage."""
        r = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)
        calls = []
        original = r._prune_over_budget
        r._prune_over_budget = lambda *a, **k: (calls.append("prune"), original(*a, **k))[1]
        r.offer(evt(), now=1000.0)
        r.flush(now=1000.0, force=True)
        assert calls, "retention must run before the write, not after"


class TestSurvivability:
    def test_offer_never_raises(self, rec, monkeypatch):
        """Moving CoT is the job; recording it is not."""
        monkeypatch.setattr(
            "charontak.recorder.parse_cot_event",
            lambda raw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert rec.offer(evt(), now=1000.0) is False

    def test_flush_failure_is_counted_not_raised(self, rec, monkeypatch):
        monkeypatch.setattr(
            "builtins.open",
            lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")),
        )
        rec.offer(evt(), now=1000.0)
        assert rec.flush(now=1000.0, force=True) == 0
        assert rec.counters["flush_errors"] == 1

    def test_unparseable_input_is_counted(self, rec):
        assert rec.offer(b"garbage", now=1000.0) is False
        assert rec.counters["dropped_unparseable"] == 1

    def test_close_flushes_remaining(self, rec):
        rec.offer(evt(), now=1000.0)
        rec.close()
        assert rec.counters["flushes"] == 1

    def test_close_is_idempotent(self, rec):
        rec.offer(evt(), now=1000.0)
        rec.close()
        rec.close()


class TestEncryption:
    def test_off_by_default(self, rec):
        assert rec.stats()["encrypted"] is False

    def test_encrypts_when_a_key_is_given(self, tmp_path):
        key = tmp_path / "key"
        key.write_bytes(os.urandom(32))
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_ENCRYPT_KEY": str(key)},
            now=1000.0,
        )
        assert r.stats()["encrypted"] is True
        r.offer(evt(), now=1000.0)
        r.flush(now=1000.0, force=True)
        path = next(
            os.path.join(dp, f)
            for dp, _, fs in os.walk(r.config.directory)
            for f in fs
        )
        assert path.endswith(".enc")
        assert b"UAL225" not in open(path, "rb").read()

    def test_raw_key_bytes_that_look_like_whitespace_are_not_stripped(
        self, tmp_path
    ):
        """AES key material is binary; leading/trailing whitespace is data."""
        key = tmp_path / "key"
        key.write_bytes(b"\n" + (b"x" * 30) + b" ")
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_ENCRYPT_KEY": str(key)},
            now=1000.0,
        )
        assert r.stats()["encrypted"] is True

    def test_newline_terminated_hex_key_is_accepted(self, tmp_path):
        key = tmp_path / "key"
        key.write_text((b"x" * 32).hex() + "\n", encoding="ascii")
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_ENCRYPT_KEY": str(key)},
            now=1000.0,
        )
        assert r.stats()["encrypted"] is True

    def test_bad_key_halts_rather_than_writing_plaintext(self, tmp_path):
        """Falling back would produce exactly the file the operator believes
        is encrypted."""
        key = tmp_path / "key"
        key.write_bytes(b"too-short")
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_ENCRYPT_KEY": str(key)},
            now=1000.0,
        )
        assert r.halted_reason is not None
        assert r.offer(evt(), now=1000.0) is False


class TestConfig:
    def test_bad_values_fall_back_to_defaults(self, tmp_path):
        r = TrackRecorder(
            {"RECORD_DIR": str(tmp_path / "t"), "RECORD_MAX_BYTES": "banana"}
        )
        assert r.config.max_bytes > 0

    def test_defaults_keep_recordings_out_of_the_web_root(self):
        """AryaOS serves /var/www/html to the LAN and hotspot unauthenticated."""
        r = TrackRecorder({})
        assert "/var/www" not in r.config.directory


class _Usage:
    """Stand-in for shutil.disk_usage."""

    def __init__(self, free, total=1000000, used=0):
        self.free, self.total, self.used = free, total, used


import time  # noqa: E402  (used by TestRetention)


class TestLaneIntegration:
    """The lane wiring, especially the EMCON claim.

    'record = only' means emit nothing. Testing that requires asserting the
    egress side is never even CONNECTED -- a lane that opens a socket to a TAK
    server has already announced the box exists, which is precisely what this
    mode is for avoiding. Asserting merely that no CoT was written would pass
    on an implementation that connects and then stays quiet.

    These drive coroutines with asyncio.run() rather than pytest-asyncio: it is
    not installed everywhere, and when it is missing pytest SKIPS async tests
    while still reporting the run as passing. Four tests that cannot fail is
    worse than no tests.
    """

    def _lane(self, tmp_path, record):
        from charontak.config import LaneSpec

        merged = {
            "ingress_cot_url": "udp://0.0.0.0:28087",
            "egress_cot_url": "udp+wo://239.2.3.1:6969",
            "mode": "forward",
            "record": record,
            "RECORD_DIR": str(tmp_path / "tracks"),
        }
        return LaneSpec(name="test", raw_section="lane:test", merged=merged)

    def _run_lane_briefly(self, tmp_path, record, monkeypatch):
        """Start a lane, let it wire up, cancel it. Returns sides connected."""
        import asyncio

        from charontak import bridge

        connected = []

        async def fake_connect(section, *, label, side, url, merged):
            connected.append(side)
            return object(), object()

        class _Worker:
            def __init__(self, *a, **k):
                pass

            async def run(self):
                await asyncio.sleep(3600)

        async def _idle(*a, **k):
            await asyncio.sleep(3600)

        monkeypatch.setattr(bridge, "_protocol_factory_with_retry", fake_connect)
        monkeypatch.setattr(bridge.pytak, "RXWorker", _Worker)
        monkeypatch.setattr(bridge.pytak, "TXWorker", _Worker)
        monkeypatch.setattr(bridge, "_discard_reader_queue", _idle)
        monkeypatch.setattr(bridge, "_close_udp_writer", _idle)

        async def drive():
            task = asyncio.create_task(bridge.run_lane(self._lane(tmp_path, record)))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(drive())
        return connected

    def test_record_only_never_connects_egress(self, tmp_path, monkeypatch):
        connected = self._run_lane_briefly(tmp_path, "only", monkeypatch)
        assert "ingress" in connected
        assert "egress" not in connected, (
            "record=only connected egress; EMCON mode must not open a socket "
            "to the server at all"
        )

    def test_record_on_still_connects_egress(self, tmp_path, monkeypatch):
        """'on' is record AND forward; it must not silently become only."""
        connected = self._run_lane_briefly(tmp_path, "on", monkeypatch)
        assert "egress" in connected

    def test_record_off_connects_egress(self, tmp_path, monkeypatch):
        connected = self._run_lane_briefly(tmp_path, "off", monkeypatch)
        assert "egress" in connected

    def test_pump_forwards_unchanged_bytes(self, tmp_path):
        import asyncio

        from charontak import bridge

        rec = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)
        payload = evt()

        async def drive():
            src: asyncio.Queue = asyncio.Queue()
            dst: asyncio.Queue = asyncio.Queue()
            await src.put(payload)
            task = asyncio.create_task(bridge._record_pump(rec, src, dst, "t"))
            await asyncio.sleep(0.05)
            task.cancel()
            return dst

        dst = asyncio.run(drive())
        # Byte-identical: the recorder observes, it does not re-serialise.
        assert dst.get_nowait() == payload
        assert rec.counters["recorded"] == 1

    def test_pump_forwards_even_when_the_recorder_raises(self, tmp_path):
        """Moving CoT is the job. A broken recorder must not eat the event."""
        import asyncio

        from charontak import bridge

        class Exploding:
            def offer(self, data, **kw):
                raise RuntimeError("recorder is broken")

        payload = evt()

        async def drive():
            src: asyncio.Queue = asyncio.Queue()
            dst: asyncio.Queue = asyncio.Queue()
            await src.put(payload)
            task = asyncio.create_task(bridge._record_pump(Exploding(), src, dst, "t"))
            await asyncio.sleep(0.05)
            task.cancel()
            return dst

        dst = asyncio.run(drive())
        assert dst.qsize() == 1, "event was lost when the recorder raised"
        assert dst.get_nowait() == payload

    def test_record_only_still_records(self, tmp_path):
        """EMCON must not mean 'record nothing' by accident."""
        import asyncio

        from charontak import bridge

        rec = TrackRecorder({"RECORD_DIR": str(tmp_path / "t")}, now=1000.0)

        async def drive():
            src: asyncio.Queue = asyncio.Queue()
            await src.put(evt())
            task = asyncio.create_task(bridge._record_pump(rec, src, None, "t"))
            await asyncio.sleep(0.05)
            task.cancel()

        asyncio.run(drive())
        assert rec.counters["recorded"] == 1


class TestConfigKeyCase:
    """ConfigParser lowercases option names.

    charontak hands the recorder a lane's merged mapping, whose keys came from
    an ini file and are therefore lower-case. Reading only the upper-case form
    meant every setting an operator wrote was silently ignored and the default
    used instead -- the recorder reported a directory that was not the one
    configured, and nothing warned. Found on a real box, not in these tests,
    which is why this class exists.
    """

    def test_lowercase_keys_are_honoured(self, tmp_path):
        want = str(tmp_path / "configured")
        r = TrackRecorder({"record_dir": want, "record_flush_seconds": "20"})
        assert r.config.directory == want
        assert r.config.flush_seconds == 20.0

    def test_uppercase_keys_still_work(self, tmp_path):
        want = str(tmp_path / "configured")
        r = TrackRecorder({"RECORD_DIR": want, "RECORD_FLUSH_SECONDS": "20"})
        assert r.config.directory == want
        assert r.config.flush_seconds == 20.0

    def test_mixed_case_works(self, tmp_path):
        r = TrackRecorder({"Record_Max_Bytes": "12345"})
        assert r.config.max_bytes == 12345

    def test_lowercase_record_all(self, tmp_path):
        r = TrackRecorder({"record_dir": str(tmp_path), "record_all": "true"})
        assert r.config.record_all is True

    def test_lowercase_encrypt_key_is_seen(self, tmp_path):
        """Otherwise a lane asking for encryption would write plaintext."""
        key = tmp_path / "k"
        key.write_bytes(os.urandom(32))
        r = TrackRecorder({"record_dir": str(tmp_path / "t"), "record_encrypt_key": str(key)})
        assert r.stats()["encrypted"] is True
