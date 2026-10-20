"""Exceptions raised by the Cantivia client.

Everything here derives from ``CantiviaError``. An invalid argument is not one of
these: it raises ``ValueError`` (or pydantic's ``ValidationError``, a ``ValueError``
subclass) before any request is sent. ``APIError`` covers failed requests:
``APIConnectionError`` (with ``APITimeoutError`` beneath it) when no usable response
arrived, and ``APIStatusError`` when the API answered with an error status, with one
subclass per status a caller is likely to branch on. ``VerificationError`` is an
``error`` event inside a result stream.

Both carry ``request_id``, the id the API gave the request. Quote it when you contact
support: it finds every record of that request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ._retry import parse_retry_after


class CantiviaError(Exception):
    """Base class for the errors this package raises, other than ``ValueError`` for an invalid argument."""


class APIError(CantiviaError):
    """A request to the API did not produce a usable response."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class APIConnectionError(APIError):
    """The API could not be reached, or the connection failed mid-response."""

    def __init__(self, message: str = "Connection error.") -> None:
        super().__init__(message)


class APITimeoutError(APIConnectionError):
    """The request timed out.

    A timeout after the request was sent is never retried: the service may already
    be doing the work. Long fact-checks are best consumed with a ``*_stream`` method.
    """

    def __init__(self, message: str = "Request timed out.") -> None:
        super().__init__(message)


class APIStatusError(APIError):
    """The API answered with an HTTP error status.

    Attributes:
        status_code: The HTTP status.
        code: The API's machine-readable error code (``error.code`` in the body),
            stable across releases and safe to branch on. None if the body had none.
        message: The API's human-readable message.
        retry_after: Seconds the API asked the caller to wait (``Retry-After``),
            or None.
        body: The decoded JSON body, or the raw text when it was not JSON.
        request_id: The id the API gave the request (the ``request-id`` header), or
            None if the response carried none. Quote it when you contact support.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        retry_after: float | None = None,
        body: Any = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        self.body = body
        self.request_id = request_id
        super().__init__(message)

    def __str__(self) -> str:
        label = f"{self.status_code} {self.code}" if self.code else str(self.status_code)
        return f"{label}: {self.message}{_quoted(self.request_id)}"


class BadRequestError(APIStatusError):
    """The request was malformed (HTTP 400)."""


class AuthenticationError(APIStatusError):
    """The API key is missing, unknown, revoked or expired (HTTP 401)."""


class PaymentRequiredError(APIStatusError):
    """The account has no active plan or pilot for this product (HTTP 402)."""


class PermissionDeniedError(APIStatusError):
    """The key is valid but lacks the permission this request needs (HTTP 403).

    Named to avoid shadowing Python's built-in ``PermissionError``.
    """


class NotFoundError(APIStatusError):
    """The resource does not exist, or belongs to another account (HTTP 404)."""


class ConflictError(APIStatusError):
    """The request conflicts with the account's current state (HTTP 409)."""


class RequestTooLargeError(APIStatusError):
    """The request body is larger than the API accepts (HTTP 413)."""


class UnprocessableEntityError(APIStatusError):
    """The request was well-formed but a value in it was refused (HTTP 422)."""


class RateLimitError(APIStatusError):
    """The rate limit or the plan's allowance was reached (HTTP 429).

    ``code`` tells the two apart: ``rate_limited`` clears after ``retry_after``
    seconds; an exhausted allowance does not clear by waiting.
    """


class InternalServerError(APIStatusError):
    """The API failed to handle the request (HTTP 5xx)."""


class ServiceUnavailableError(InternalServerError):
    """The API is at capacity and refused the request before doing any work (HTTP 503)."""


class VerificationError(CantiviaError):
    """A result stream carried an ``error`` event instead of a result.

    ``request_id`` is the id the API gave the stream's request, or None.
    """

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        self.message = message
        self.request_id = request_id
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.message}{_quoted(self.request_id)}"


def _quoted(request_id: str | None) -> str:
    return f" (request id {request_id})" if request_id else ""


_STATUS_ERRORS: dict[int, type[APIStatusError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    413: RequestTooLargeError,
    422: UnprocessableEntityError,
    429: RateLimitError,
    503: ServiceUnavailableError,
}


def make_status_error(status_code: int, text: str, headers: Mapping[str, str] | None = None) -> APIStatusError:
    """Build the typed error for an HTTP error response from its status, body and headers."""
    body: Any = text
    code: str | None = None
    message = text[:500] or f"HTTP {status_code}"
    request_id = headers.get("request-id") if headers is not None else None
    try:
        body = json.loads(text)
    except ValueError:
        pass
    else:
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            if isinstance(error.get("code"), str):
                code = error["code"]
            if isinstance(error.get("message"), str):
                message = error["message"]
        if not request_id and isinstance(body, dict) and isinstance(body.get("request_id"), str):
            request_id = body["request_id"]
    retry_after = parse_retry_after(headers.get("retry-after") if headers is not None else None)
    cls = _STATUS_ERRORS.get(status_code)
    if cls is None:
        cls = InternalServerError if status_code >= 500 else APIStatusError
    return cls(
        message, status_code=status_code, code=code, retry_after=retry_after, body=body, request_id=request_id or None
    )
