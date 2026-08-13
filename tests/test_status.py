"""Tests for COTBridge's aggregate lane health surface."""

from cotbridge.status import BridgeStatus


def test_bridge_status_reports_lane_and_traffic(tmp_path) -> None:
    status = BridgeStatus(path=str(tmp_path / "status.json"))
    status.add_lane(
        "site-output", "udp+ro://127.0.0.1:28087", "tls://example:8089", "forward"
    )
    status.connection("site-output", "ingress", "connected")
    status.connection("site-output", "output", "retrying", "offline")
    status.received()
    status.emitted()
    status.refresh()

    doc = status.writer.as_dict()
    assert doc["app"] == "cotbridge"
    assert doc["health"]["state"] == "degraded"
    assert doc["output"]["state"] == "retrying"
    assert doc["input"]["total"] == 1
    assert doc["output"]["total"] == 1
    assert doc["lanes"]["site-output"]["output"]["detail"] == "offline"


def test_bridge_status_redacts_enrollment_tokens(tmp_path) -> None:
    status = BridgeStatus(path=str(tmp_path / "status.json"))
    status.add_lane(
        "site-output",
        "udp+ro://127.0.0.1:28087",
        "tak://com.atakmap.app/enroll?host=example&token=secret",
        "forward",
    )
    doc = status.writer.as_dict()
    assert "secret" not in doc["lanes"]["site-output"]["output"]["url"]
