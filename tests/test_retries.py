"""The retry policy: only what provably never reached the service is sent again."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from cantivia import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncCantiviaClient,
    CantiviaClient,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
)
from cantivia._retry import MAX_RETRY_DELAY, parse_retry_after, retry_delay

USAGE_JSON = {
    "key_id": "abc123",
    "requests": {"window": 1, "today": 1, "month": 1},
    "rate_limit": {"limit": 100, "remaining": 99, "window_seconds": 60},
    "verdicts": {},
    "latency_ms": {"p50": 1.0, "p95": 1.0, "samples": 1},
}

DONE_SSE = (
    'data: {"type":"done","result":{"claim":"c","verdict":"fake","confidence":0.9,"reasoning":"r",'
    '"citations":[],"cacheHit":false,"route":"route-a","processingMs":1}}\n\n'
)


def _scripted(*steps):
    """A transport handler answering each attempt with the next step (repeating the last).

    A step is a status code (answered with an empty error body), an exception to raise,
    or an ``httpx.Response``.
    """
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        step = steps[min(len(attempts), len(steps)) - 1]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        if step >= 400:
            return httpx.Response(step, json={"error": {"code": "x", "message": "refused"}})
        return httpx.Response(step, json=USAGE_JSON)

    return handler, attempts


def _client(handler, **kwargs) -> CantiviaClient:
    inner = httpx.Client(transport=httpx.MockTransport(handler))
    return CantiviaClient(api_key="ctv_test", base_url="http://gw", http_client=inner, **kwargs)


def _async_client(handler, **kwargs) -> AsyncCantiviaClient:
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncCantiviaClient(api_key="ctv_test", base_url="http://gw", http_client=inner, **kwargs)


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("connection refused")


@pytest.mark.parametrize("first", [429, 503, "connect", "connect_timeout"])
def test_a_refusal_that_never_reached_the_service_is_retried(first, sleeps):
    step = {"connect": _connect_error(), "connect_timeout": httpx.ConnectTimeout("slow")}.get(first, first)
    handler, attempts = _scripted(step, 200)
    with _client(handler) as client:
        usage = client.usage()
    assert usage.key_id == "abc123"
    assert len(attempts) == 2
    assert len(sleeps) == 1


def test_retries_stop_at_max_retries_and_raise_the_last_error(sleeps):
    handler, attempts = _scripted(429)
    with _client(handler) as client, pytest.raises(RateLimitError):
        client.usage()
    # The default is two retries: three attempts in all.
    assert len(attempts) == 3
    assert len(sleeps) == 2


def test_503_exhausted_raises_service_unavailable(sleeps):
    handler, attempts = _scripted(503)
    with _client(handler, max_retries=1) as client, pytest.raises(ServiceUnavailableError) as exc:
        client.usage()
    assert isinstance(exc.value, InternalServerError)
    assert len(attempts) == 2


def test_connect_errors_exhausted_raise_api_connection_error(sleeps):
    handler, attempts = _scripted(_connect_error())
    with _client(handler) as client, pytest.raises(APIConnectionError) as exc:
        client.usage()
    assert not isinstance(exc.value, APITimeoutError)
    assert len(attempts) == 3


def test_connect_timeouts_exhausted_raise_api_timeout_error(sleeps):
    handler, attempts = _scripted(httpx.ConnectTimeout("slow"))
    with _client(handler) as client, pytest.raises(APITimeoutError):
        client.usage()
    assert len(attempts) == 3


def test_max_retries_zero_disables_retrying(sleeps):
    handler, attempts = _scripted(503, 200)
    with _client(handler, max_retries=0) as client, pytest.raises(ServiceUnavailableError):
        client.usage()
    assert len(attempts) == 1
    assert sleeps == []


def test_max_retries_can_be_set_per_call(sleeps):
    handler, attempts = _scripted(429)
    with _client(handler, max_retries=0) as client, pytest.raises(RateLimitError):
        client.usage(max_retries=3)
    assert len(attempts) == 4

    handler, attempts = _scripted(429, 200)
    with _client(handler) as client, pytest.raises(RateLimitError):
        client.usage(max_retries=0)
    assert len(attempts) == 1


def test_a_negative_max_retries_is_refused():
    with pytest.raises(ValueError):
        CantiviaClient(api_key="ctv_test", max_retries=-1)


@pytest.mark.parametrize("status", [500, 502, 504, 400, 401, 402, 404, 409, 422])
def test_other_statuses_are_never_retried(status, sleeps):
    handler, attempts = _scripted(status, 200)
    with _client(handler) as client, pytest.raises(APIStatusError) as exc:
        client.usage()
    assert exc.value.status_code == status
    assert len(attempts) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("slow"), httpx.WriteTimeout("slow"), httpx.ReadError("reset"), httpx.RemoteProtocolError("x")],
)
def test_a_failure_after_the_request_was_sent_is_never_retried(error, sleeps):
    # The service may already be doing (and billing) the work.
    handler, attempts = _scripted(error, 200)
    with _client(handler) as client, pytest.raises(APIConnectionError):
        client.usage()
    assert len(attempts) == 1


def test_a_read_timeout_raises_api_timeout_error(sleeps):
    handler, _ = _scripted(httpx.ReadTimeout("slow"))
    with _client(handler) as client, pytest.raises(APITimeoutError):
        client.usage()


def test_retry_after_seconds_is_honoured(sleeps):
    refused = httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"code": "rate_limited"}})
    handler, _ = _scripted(refused, 200)
    with _client(handler) as client:
        client.usage()
    assert sleeps == [7.0]


def test_retry_after_http_date_is_honoured(sleeps):
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    refused = httpx.Response(503, headers={"Retry-After": when}, json={"error": {"code": "overloaded"}})
    handler, _ = _scripted(refused, 200)
    with _client(handler) as client:
        client.usage()
    assert len(sleeps) == 1
    assert 25 <= sleeps[0] <= 30


def test_a_retry_after_over_60_seconds_falls_back_to_backoff(sleeps):
    refused = httpx.Response(429, headers={"Retry-After": "3600"}, json={"error": {"code": "rate_limited"}})
    handler, _ = _scripted(refused, 200)
    with _client(handler) as client:
        client.usage()
    assert len(sleeps) == 1
    assert 0.25 <= sleeps[0] <= 0.5


def test_backoff_doubles_from_half_a_second_with_equal_jitter(sleeps):
    handler, _ = _scripted(503)
    with _client(handler, max_retries=4) as client, pytest.raises(ServiceUnavailableError):
        client.usage()
    for attempt, delay in enumerate(sleeps):
        full = min(0.5 * 2**attempt, MAX_RETRY_DELAY)
        assert full / 2 <= delay <= full


def test_retry_delay_bounds():
    assert retry_delay(0, None, rand=lambda: 0.0) == 0.25
    assert retry_delay(0, None, rand=lambda: 1.0) == 0.5
    assert retry_delay(1, None, rand=lambda: 0.0) == 0.5
    assert retry_delay(2, None, rand=lambda: 1.0) == 2.0
    # Capped at 8 s, so the jittered delay never exceeds it.
    assert retry_delay(10, None, rand=lambda: 1.0) == MAX_RETRY_DELAY
    assert retry_delay(10, None, rand=lambda: 0.0) == MAX_RETRY_DELAY / 2
    assert retry_delay(0, 12.0) == 12.0
    assert retry_delay(0, 61.0, rand=lambda: 0.0) == 0.25


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, None), ("", None), ("12", 12.0), (" 3 ", 3.0), ("1.5", 1.5), ("-1", None), ("soon", None), ("nan", None)],
)
def test_parse_retry_after(header, expected):
    assert parse_retry_after(header) == expected


def test_parse_retry_after_http_date_in_the_past_is_zero():
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


def test_a_stream_refused_before_its_body_is_retried(sleeps):
    ok = httpx.Response(200, text=DONE_SSE, headers={"content-type": "text/event-stream"})
    handler, attempts = _scripted(503, ok)
    with _client(handler) as client:
        result = client.verify("c")
    assert result.claim == "c"
    assert len(attempts) == 2


class _BrokenStream(httpx.SyncByteStream):
    """A response body that delivers one event and then loses the connection."""

    def __iter__(self):
        yield b'data: {"type":"routing","action":"route-a","difficulty":0.5}\n\n'
        raise httpx.ReadError("connection reset")


def test_a_stream_that_fails_after_its_body_started_is_not_retried(sleeps):
    broken = httpx.Response(200, stream=_BrokenStream(), headers={"content-type": "text/event-stream"})
    handler, attempts = _scripted(broken, 200)
    with _client(handler) as client:
        chunks = client.verify_stream("c")
        assert next(chunks).type == "routing"
        with pytest.raises(APIConnectionError):
            next(chunks)
    assert len(attempts) == 1
    assert sleeps == []


def test_async_retries_then_succeeds(sleeps):
    handler, attempts = _scripted(_connect_error(), 429, 200)

    async def run():
        async with _async_client(handler) as client:
            return await client.usage()

    usage = asyncio.run(run())
    assert usage.key_id == "abc123"
    assert len(attempts) == 3
    assert len(sleeps) == 2


def test_async_does_not_retry_a_500(sleeps):
    handler, attempts = _scripted(500, 200)

    async def run():
        async with _async_client(handler) as client:
            await client.usage()

    with pytest.raises(InternalServerError):
        asyncio.run(run())
    assert len(attempts) == 1


def test_async_max_retries_zero_disables_retrying(sleeps):
    handler, attempts = _scripted(503, 200)

    async def run():
        async with _async_client(handler, max_retries=0) as client:
            await client.usage()

    with pytest.raises(ServiceUnavailableError):
        asyncio.run(run())
    assert len(attempts) == 1


def test_async_stream_refused_before_its_body_is_retried(sleeps):
    ok = httpx.Response(200, text=DONE_SSE, headers={"content-type": "text/event-stream"})
    handler, attempts = _scripted(429, ok)

    async def run():
        async with _async_client(handler) as client:
            return await client.verify("c")

    assert asyncio.run(run()).claim == "c"
    assert len(attempts) == 2
