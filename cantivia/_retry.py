"""When a failed request may be sent again, and how long to wait first.

Only failures that provably never reached the service are retried. A fact-check
that started runs its work once per attempt and is billed once per attempt, so a
read timeout or a 500 is surfaced to the caller rather than retried: the work may
already be under way.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime

import httpx

DEFAULT_MAX_RETRIES = 2

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
# A longer Retry-After is not waited out inside a call; the exponential backoff is
# used instead, and a caller who wants to wait longer can do so on the raised error.
MAX_RETRY_AFTER = 60.0

# 429: refused by the rate limiter. 503: refused at the door for capacity, before
# any work began. Both are answered without doing the work.
RETRYABLE_STATUS_CODES = frozenset({429, 503})


def is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES


def is_retryable_exception(exc: BaseException) -> bool:
    """True for a failure to connect: refused, reset, DNS failure or connect timeout.

    Anything later (a read timeout, a dropped connection mid-response) may have
    reached the service, so it is not retried.
    """
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds from a `Retry-After` header: delta-seconds or an HTTP date. None if unusable."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        seconds = when.timestamp() - (time.time() if now is None else now)
        return max(seconds, 0.0)
    if math.isnan(seconds) or seconds < 0:
        return None
    return seconds


def retry_delay(attempt: int, retry_after: float | None, *, rand: Callable[[], float] = random.random) -> float:
    """Seconds to wait before retry number ``attempt + 1`` (``attempt`` counts from 0).

    The server's `Retry-After` wins when it is present and at most 60 s. Otherwise
    the delay doubles from 0.5 s up to 8 s with equal jitter: half of it fixed, half
    random, so clients refused together do not come back together.
    """
    if retry_after is not None and retry_after <= MAX_RETRY_AFTER:
        return retry_after
    delay = min(INITIAL_RETRY_DELAY * (2**attempt), MAX_RETRY_DELAY)
    return delay / 2 + rand() * (delay / 2)
