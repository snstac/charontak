"""Tests for log-safe COT URL redaction."""

from charontak.log_urls import redact_cot_url


def test_redact_cot_url_masks_token() -> None:
    url = (
        "tak://com.atakmap.app/enroll?host=example.com"
        "&username=user&token=supersecret"
    )
    out = redact_cot_url(url)
    assert "supersecret" not in out
    assert "token=***" in out
    assert "username=user" in out
    assert "host=example.com" in out


def test_redact_cot_url_masks_password() -> None:
    url = "tak://com.atakmap.app/enroll?password=abc123&host=x"
    out = redact_cot_url(url)
    assert "abc123" not in out
    assert "password=***" in out


def test_redact_cot_url_unchanged_without_secrets() -> None:
    url = "udp+ro://239.2.3.1:6969"
    assert redact_cot_url(url) == url
