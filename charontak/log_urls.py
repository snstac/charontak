"""Helpers for logging COT URLs without leaking secrets."""

from __future__ import annotations

import re

_SENSITIVE_QUERY_KEYS = ("token", "password", "passwd", "secret")


def redact_cot_url(url: str) -> str:
    """Return a log-safe COT URL (masks enrollment tokens and similar query params)."""

    out = url
    for key in _SENSITIVE_QUERY_KEYS:
        out = re.sub(rf"(({re.escape(key)})=)[^&]+", r"\1***", out, flags=re.IGNORECASE)
    return out
