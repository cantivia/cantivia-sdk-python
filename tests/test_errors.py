"""Error mapping, timeouts and client configuration."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from cantivia import (
    DEFAULT_BASE_URL,
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncCantiviaClient,
    AuthenticationError,
    BadRequestError,
    CantiviaClient,
    CantiviaError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    RateLimitError,
    RequestTooLargeError,
    ServiceUnavailableError,
    UnprocessableEntityError,
    VerificationError,
    __version__,
)
from cantivia.errors import make_status_error
from cantivia.models import ErrorChunk

USAGE_JSON = {
    "key_id": "abc123",
    "requests": {"window": 1, "today": 1, "month": 1},
    "rate_limit": {"limit": 100, "remaining": 99, "window_seconds": 60},
    "verdicts": {},
    "latency_ms": {"p50": 1.0, "p95": 1.0, "samples": 1},
}


def _recording(status: int = 200, body: object = None, headers: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=USAGE_JSON if body is None else body, headers=headers or {})

    return handler, seen


def _client(handler, **kwargs) -> CantiviaClient:
    kwargs.setdefault("api_key", "ctv_test")
    kwargs.setdefault("base_url", "http://gw")
    return CantiviaClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0, **kwargs)


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (402, PaymentRequiredError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (413, RequestTooLargeError),
        (422, UnprocessableEntityError),
        (429, RateLimitError),
        (500, InternalServerError),
        (502, InternalServerError),
        (503, ServiceUnavailableError),
        (504, InternalServerError),
    ],
)
def test_each_status_maps_to_its_error(status, cls):
    body = {"error": {"code": "some_code", "message": "Something specific."}}
    handler, _ = _recording(status, body)
    with _client(handler) as client, pytest.raises(cls) as exc:
        client.usage()
    error = exc.value
    assert type(error) is cls
    assert isinstance(error, APIStatusError)
    assert isinstance(error, APIError)
    assert isinstance(error, CantiviaError)
    assert error.status_code == status
    assert error.code == "some_code"
    assert error.message == "Something specific."
    assert error.body == body
    assert str(error) == f"{status} some_code: Something specific."


def test_an_unlisted_4xx_is_a_plain_status_error():
    error = make_status_error(405, '{"error":{"code":"method_not_allowed","message":"No."}}')
    assert type(error) is APIStatusError
    assert error.code == "method_not_allowed"


def test_service_unavailable_is_an_internal_server_error():
    assert issubclass(ServiceUnavailableError, InternalServerError)
    assert issubclass(APITimeoutError, APIConnectionError)


def test_a_non_json_error_body_is_kept_as_text():
    error = make_status_error(502, "Upstream unavailable")
    assert isinstance(error, InternalServerError)
    assert error.code is None
    assert error.message == "Upstream unavailable"
    assert error.body == "Upstream unavailable"


def test_retry_after_is_exposed_on_the_error():
    handler, _ = _recording(429, {"error": {"code": "rate_limited", "message": "Slow down."}}, {"Retry-After": "12"})
    with _client(handler) as client, pytest.raises(RateLimitError) as exc:
        client.usage()
    assert exc.value.retry_after == 12
    assert exc.value.code == "rate_limited"


def test_retry_after_is_none_without_the_header():
    handler, _ = _recording(429, {"error": {"code": "monthly_quota_exceeded", "message": "Used up."}})
    with _client(handler) as client, pytest.raises(RateLimitError) as exc:
        client.usage()
    assert exc.value.retry_after is None


def test_a_stream_error_status_maps_to_its_error():
    handler, _ = _recording(402, {"error": {"code": "no_entitlement", "message": "Subscribe."}})
    with _client(handler) as client, pytest.raises(PaymentRequiredError) as exc:
        client.verify("c")
    assert exc.value.code == "no_entitlement"


def test_a_timeout_raises_api_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with _client(handler) as client, pytest.raises(APITimeoutError):
        client.verify("c")


def test_the_timeout_is_sent_with_each_request_and_can_be_set_per_call():
    handler, seen = _recording()
    with _client(handler, timeout=30.0) as client:
        client.usage()
        client.usage(timeout=httpx.Timeout(5.0))
    first, second = (request.extensions["timeout"] for request in seen)
    assert first == {"connect": 10.0, "read": 30.0, "write": 30.0, "pool": 30.0}
    assert second == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}


def test_the_default_timeout_is_ten_minutes_with_ten_seconds_to_connect():
    handler, seen = _recording()
    with _client(handler) as client:
        client.usage()
    assert seen[0].extensions["timeout"] == {"connect": 10.0, "read": 600.0, "write": 600.0, "pool": 600.0}


def test_user_agent_names_the_sdk_and_version():
    handler, seen = _recording()
    with _client(handler) as client:
        client.usage()
    assert seen[0].headers["user-agent"] == f"cantivia-python/{__version__}"


def test_defaults_to_the_public_api():
    client = CantiviaClient(api_key="ctv_test")
    assert client.base_url == DEFAULT_BASE_URL == "https://api.cantivia.com"
    client.close()


def test_reads_the_key_and_base_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("CANTIVIA_API_KEY", "ctv_from_env")
    monkeypatch.setenv("CANTIVIA_BASE_URL", "http://env-gw/")
    handler, seen = _recording()
    client = CantiviaClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with client:
        client.usage()
    assert str(seen[0].url) == "http://env-gw/api/v1/usage"
    assert seen[0].headers["authorization"] == "Bearer ctv_from_env"


def test_an_explicit_key_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("CANTIVIA_API_KEY", "ctv_from_env")
    handler, seen = _recording()
    with _client(handler, api_key="ctv_explicit") as client:
        client.usage()
    assert seen[0].headers["authorization"] == "Bearer ctv_explicit"


def test_a_missing_key_fails_before_sending_and_names_the_variable():
    handler, seen = _recording()
    with _client(handler, api_key=None) as client, pytest.raises(CantiviaError, match="CANTIVIA_API_KEY"):
        client.verify("c")
    assert seen == []


def test_health_needs_no_key():
    handler, seen = _recording(body={"status": "ok"})
    with _client(handler, api_key=None) as client:
        assert client.health() == {"status": "ok"}
    assert "authorization" not in seen[0].headers


def test_async_missing_key_fails_before_sending():
    handler, seen = _recording()

    async def run():
        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with AsyncCantiviaClient(base_url="http://gw", http_client=inner) as client:
            await client.usage()

    with pytest.raises(CantiviaError, match="CANTIVIA_API_KEY"):
        asyncio.run(run())
    assert seen == []


def test_async_status_error_carries_the_code():
    handler, _ = _recording(403, {"error": {"code": "insufficient_scope", "message": "Missing scope."}})

    async def run():
        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with AsyncCantiviaClient(api_key="k", base_url="http://gw", http_client=inner) as client:
            await client.verify("c")

    with pytest.raises(PermissionDeniedError) as exc:
        asyncio.run(run())
    assert exc.value.code == "insufficient_scope"


def test_a_status_error_carries_the_request_id_from_the_header():
    body = {"error": {"code": "rate_limited", "message": "Slow down."}, "request_id": "req_body"}
    handler, _ = _recording(429, body, {"request-id": "req_header"})
    with _client(handler) as client, pytest.raises(RateLimitError) as exc:
        client.usage()
    # The header wins: every response carries it, error or not.
    assert exc.value.request_id == "req_header"
    assert str(exc.value) == "429 rate_limited: Slow down. (request id req_header)"


def test_the_request_id_falls_back_to_the_body():
    body = '{"error":{"code":"unauthorized","message":"Invalid API key."},"request_id":"req_abc"}'
    error = make_status_error(401, body)
    assert error.request_id == "req_abc"
    assert str(error) == "401 unauthorized: Invalid API key. (request id req_abc)"


def test_no_request_id_leaves_the_message_alone():
    error = make_status_error(502, "Upstream unavailable")
    assert error.request_id is None
    assert str(error) == "502: Upstream unavailable"


def _stream_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        text='data: {"type":"error","message":"the check timed out"}\n\n',
        headers={"content-type": "text/event-stream", "request-id": "req_stream"},
    )


def test_a_mid_stream_error_carries_the_stream_request_id():
    with _client(_stream_handler) as client:
        chunks = list(client.verify_stream("c"))
        with pytest.raises(VerificationError) as exc:
            client.verify("c")
    assert isinstance(chunks[-1], ErrorChunk)
    assert chunks[-1].request_id == "req_stream"
    assert exc.value.request_id == "req_stream"
    assert exc.value.message == "the check timed out"
    assert str(exc.value) == "the check timed out (request id req_stream)"


def test_async_errors_carry_the_request_id():
    status_handler, _ = _recording(503, {"error": {"code": "overloaded", "message": "Busy."}}, {"request-id": "req_a"})

    async def run(handler):
        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with AsyncCantiviaClient(api_key="k", base_url="http://gw", http_client=inner, max_retries=0) as client:
            await client.verify("c")

    with pytest.raises(ServiceUnavailableError) as status_exc:
        asyncio.run(run(status_handler))
    assert status_exc.value.request_id == "req_a"
    with pytest.raises(VerificationError) as stream_exc:
        asyncio.run(run(_stream_handler))
    assert stream_exc.value.request_id == "req_stream"
