"""When to try a failed request again, and how long to wait first. Times are in seconds."""

from __future__ import annotations

import math
import random
import time
from email.utils import parsedate_to_datetime

import httpx


def should_retry_status(status: int) -> bool:
    """Statuses worth retrying: 408 (timeout), 429 (too many requests) and 5xx (Wity's side)."""
    return status in (408, 429) or status >= 500


MAX_RETRY_AFTER = 60.0
"""The longest wait the SDK accepts from a ``Retry-After`` header.

Wity's 503 asks for 90 s. That's too long to block silently, so the SDK
raises instead and the caller decides.
"""


def retry_after(headers: httpx.Headers) -> float | None:
    """Read ``Retry-After`` as seconds. It can be seconds ("1") or a date."""
    raw = headers.get("retry-after")
    if raw is None or raw.strip() == "":
        return None
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        return max(0.0, seconds) if math.isfinite(seconds) else None
    try:
        date = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, date.timestamp() - time.time())


def backoff(attempt: int) -> float:
    """Wait 0.5 s, then 1 s, 2 s... up to 8 s.

    The random part stops many clients retrying at the same moment.
    """
    return min(0.5 * 2.0 ** min(attempt, 10), 8.0) * (0.75 + random.random() * 0.25)


def delay_before_retry(attempt: int, headers: httpx.Headers) -> float | None:
    """How long to wait before the next try, or ``None`` to stop retrying."""
    from_server = retry_after(headers)
    if from_server is None:
        return backoff(attempt)
    return from_server if from_server <= MAX_RETRY_AFTER else None
