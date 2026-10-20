"""Synchronous and asynchronous clients for the Cantivia API."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator, Iterator
from contextlib import aclosing, closing
from typing import TYPE_CHECKING, Any, Literal

import httpx

from ._retry import DEFAULT_MAX_RETRIES, is_retryable_exception, is_retryable_status, parse_retry_after, retry_delay
from ._sse import aiter_sse, iter_sse
from ._version import __version__
from .errors import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    CantiviaError,
    VerificationError,
    make_status_error,
)
from .models import (
    CATALOGUE_ASSET_ID_PATTERN,
    ArticleDoneChunk,
    ArticleVerificationRequest,
    ArticleVerificationResult,
    AudioVerificationRequest,
    Batch,
    BatchAccepted,
    BatchRequest,
    CatalogueAsset,
    CatalogueDeleteResponse,
    CatalogueEnrollJob,
    CatalogueEnrollRequest,
    CatalogueListResponse,
    CatalogueNoticeRequest,
    CatalogueNoticeResponse,
    Complainant,
    DoneChunk,
    ErrorChunk,
    ImageVerificationRequest,
    MediaForensicsChunk,
    MediaForensicsResult,
    PaperMillScreenRequest,
    PaperMillScreenResult,
    PostTitrateRequest,
    ResolveRequest,
    ResolveResult,
    StreamChunk,
    Titrate,
    TitrateChunk,
    Usage,
    VerificationRequest,
    VerificationResult,
    VideoVerificationRequest,
    parse_chunk,
)

if TYPE_CHECKING:
    from typing_extensions import Self

DEFAULT_BASE_URL = "https://api.cantivia.com"
API_KEY_ENV = "CANTIVIA_API_KEY"
BASE_URL_ENV = "CANTIVIA_BASE_URL"

DEFAULT_CONNECT_TIMEOUT = 10.0
# A request times out after this many seconds without data. A thorough check can take
# minutes; a stream sends progress as it goes, so it stays within the limit.
DEFAULT_TIMEOUT = httpx.Timeout(600.0, connect=DEFAULT_CONNECT_TIMEOUT)

# Enrolment takes seconds to minutes per asset, so a waited enrolment reads its job at
# this pace (seconds), for at most this long.
ENROLL_POLL_INTERVAL = 2.0
ENROLL_MAX_WAIT = 600.0

# Batch items run in the background a few at a time, each taking tens of seconds.
BATCH_POLL_INTERVAL = 5.0
BATCH_MAX_WAIT = 3600.0

_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

_MISSING_API_KEY = (
    f"No API key: pass api_key to the client or set the {API_KEY_ENV} environment variable. "
    "Create a key from your Cantivia dashboard."
)

Modality = Literal["image", "audio", "video"]
Priority = Literal["standard", "high"]


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _asleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _as_timeout(timeout: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(timeout, connect=min(timeout, DEFAULT_CONNECT_TIMEOUT))


def _checked_retries(max_retries: int) -> int:
    if max_retries < 0:
        raise ValueError("max_retries must be 0 or more")
    return max_retries


def _transport_error(exc: httpx.TransportError) -> APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError()
    detail = str(exc) or type(exc).__name__
    return APIConnectionError(f"Connection error: {detail}")


def _iter_lines(response: httpx.Response) -> Iterator[str]:
    try:
        yield from response.iter_lines()
    except httpx.TransportError as exc:
        raise _transport_error(exc) from exc


async def _aiter_lines(response: httpx.Response) -> AsyncIterator[str]:
    try:
        async for line in response.aiter_lines():
            yield line
    except httpx.TransportError as exc:
        raise _transport_error(exc) from exc


def _with_request_id(chunk: StreamChunk, request_id: str | None) -> StreamChunk:
    """Stamp the stream's request id onto an error event, which then carries it."""
    if isinstance(chunk, ErrorChunk):
        chunk.request_id = request_id
    return chunk


def _final(chunks: Generator[StreamChunk, None, None], terminal: type, what: str) -> Any:
    """The result carried by a finished stream's terminal chunk, or the error it carried."""
    result = None
    with closing(chunks) as stream:
        for chunk in stream:
            if isinstance(chunk, ErrorChunk):
                raise VerificationError(chunk.message, request_id=chunk.request_id)
            if isinstance(chunk, terminal):
                result = chunk.result
    if result is None:
        raise CantiviaError(f"{what} stream ended without a result")
    return result


async def _afinal(chunks: AsyncGenerator[StreamChunk, None], terminal: type, what: str) -> Any:
    result = None
    async with aclosing(chunks) as stream:
        async for chunk in stream:
            if isinstance(chunk, ErrorChunk):
                raise VerificationError(chunk.message, request_id=chunk.request_id)
            if isinstance(chunk, terminal):
                result = chunk.result
    if result is None:
        raise CantiviaError(f"{what} stream ended without a result")
    return result


def _claim_payload(claim: str, language: str, priority: Priority, post_url: str | None) -> dict[str, Any]:
    request = VerificationRequest(claim=claim, language=language, priority=priority, post_url=post_url)
    return request.model_dump(by_alias=True, exclude_none=True)


def _article_payload(article: str, language: str, priority: Priority) -> dict[str, Any]:
    return ArticleVerificationRequest(claim=article, language=language, priority=priority).model_dump()


def _image_payload(image: str, language: str, priority: Priority, forensics_only: bool) -> dict[str, Any]:
    request = ImageVerificationRequest(image=image, language=language, priority=priority, forensics_only=forensics_only)
    return request.model_dump(by_alias=True)


def _media_payload(media: str, modality: Modality, mime: str | None) -> dict[str, Any]:
    # An image through this path is forensics-only: no OCR and no fact-check run.
    if modality == "image":
        return ImageVerificationRequest(image=media, forensics_only=True).model_dump(by_alias=True)
    model = AudioVerificationRequest if modality == "audio" else VideoVerificationRequest
    return model(media=media, mime=mime).model_dump(exclude_none=True)


def _post_payload(post_url: str) -> dict[str, Any]:
    return PostTitrateRequest(post_url=post_url).model_dump(by_alias=True)


def _enroll_payload(assets: list[CatalogueAsset] | list[dict[str, str]], source: str) -> dict[str, Any]:
    return CatalogueEnrollRequest.model_validate({"assets": assets, "source": source}).model_dump()


def _notice_payload(
    name: str, media: str, infringing_url: str, complainant: Complainant | dict[str, str], host: str
) -> dict[str, Any]:
    request = CatalogueNoticeRequest.model_validate(
        {"name": name, "media": media, "infringingUrl": infringing_url, "complainant": complainant, "host": host}
    )
    return request.model_dump(by_alias=True)


def _resolve_payload(follow_up: str, prior_claims: list[str] | None) -> dict[str, Any]:
    request = ResolveRequest(follow_up=follow_up, prior_claims=prior_claims or [])
    return request.model_dump(by_alias=True)


def _screen_payload(title: str, abstract: str, budget: float | None) -> dict[str, Any]:
    return PaperMillScreenRequest(title=title, abstract=abstract, budget=budget).model_dump(exclude_none=True)


def _batch_payload(claims: list[str], kind: Literal["claim", "article"]) -> dict[str, Any]:
    return BatchRequest(claims=claims, kind=kind).model_dump()


def _batch_headers(idempotency_key: str | None) -> dict[str, str] | None:
    return {"Idempotency-Key": idempotency_key} if idempotency_key else None


def _asset_path(asset_id: str) -> str:
    # Checked before sending because the id becomes part of the URL.
    if not re.fullmatch(CATALOGUE_ASSET_ID_PATTERN, asset_id):
        raise ValueError(
            "invalid catalogue asset id: expected the 64-character lowercase hex SHA-256 of the asset's bytes"
        )
    return f"/api/v1/catalogue/assets/{asset_id}"


def _job_path(job_id: str) -> str:
    # Checked before sending because the id becomes part of the URL.
    if not re.fullmatch(_UUID_PATTERN, job_id):
        raise ValueError("invalid enrolment job id: expected the UUID the enrolment answered with")
    return f"/api/v1/catalogue/jobs/{job_id}"


def _batch_path(batch_id: str) -> str:
    if not re.fullmatch(_UUID_PATTERN, batch_id):
        raise ValueError("invalid batch id: expected the UUID submit_batch answered with")
    return f"/api/v1/batch/{batch_id}"


def _enroll_timed_out(job: CatalogueEnrollJob, max_wait: float) -> CantiviaError:
    return CantiviaError(
        f"enrolment job {job.job_id} did not finish within {max_wait:g}s; read it on with get_enrollment_job"
    )


def _batch_timed_out(batch: Batch, max_wait: float) -> CantiviaError:
    return CantiviaError(f"batch {batch.batch_id} did not finish within {max_wait:g}s; read it on with get_batch")


class _BaseClient:
    """Configuration shared by the sync and async clients."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float | httpx.Timeout,
        max_retries: int,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV)) or None
        self.base_url = (base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = _as_timeout(timeout)
        self.max_retries = _checked_retries(max_retries)

    def _headers(self, auth: bool, extra: dict[str, str] | None) -> dict[str, str]:
        headers = {"User-Agent": f"cantivia-python/{__version__}"}
        if auth:
            # Checked per request rather than at construction, so health() works without a key.
            if not self.api_key:
                raise CantiviaError(_MISSING_API_KEY)
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            headers.update(extra)
        return headers

    def _timeout_for(self, timeout: float | httpx.Timeout | None) -> httpx.Timeout:
        return self.timeout if timeout is None else _as_timeout(timeout)

    def _retries_for(self, max_retries: int | None) -> int:
        return self.max_retries if max_retries is None else _checked_retries(max_retries)


class CantiviaClient(_BaseClient):
    """Synchronous client for the Cantivia API.

    Args:
        api_key: Your API key, sent as ``Authorization: Bearer <key>``. Defaults to
            the ``CANTIVIA_API_KEY`` environment variable. Only ``health`` works
            without one.
        base_url: Defaults to ``CANTIVIA_BASE_URL``, then ``https://api.cantivia.com``.
        timeout: Seconds a request may go without receiving data, or an
            ``httpx.Timeout``. Defaults to 600 s without data, with 10 s to connect.
            A timeout raises ``APITimeoutError``.
        max_retries: Retries for requests that never reached the service (connection
            failures, 429 and 503). Defaults to 2; 0 disables them.
        http_client: An ``httpx.Client`` to send requests with, e.g. one configured
            with a proxy. The settings above still apply to every request.

    Every request method also takes ``timeout`` and ``max_retries`` to override these
    for one call. Use the client as a context manager, or call ``close()``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = http_client if http_client is not None else httpx.Client()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connections."""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
        stream: bool = False,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        retries = self._retries_for(max_retries)
        attempt = 0
        while True:
            request = self._client.build_request(
                method,
                self.base_url + path,
                json=json,
                headers=self._headers(auth, headers),
                timeout=self._timeout_for(timeout),
            )
            try:
                response = self._client.send(request, stream=stream)
            except httpx.TransportError as exc:
                if is_retryable_exception(exc) and attempt < retries:
                    _sleep(retry_delay(attempt, None))
                    attempt += 1
                    continue
                raise _transport_error(exc) from exc
            if response.status_code < 400:
                return response
            if is_retryable_status(response.status_code) and attempt < retries:
                response.close()
                _sleep(retry_delay(attempt, parse_retry_after(response.headers.get("retry-after"))))
                attempt += 1
                continue
            raise self._status_error(response)

    def _status_error(self, response: httpx.Response) -> APIStatusError | APIConnectionError:
        try:
            response.read()
        except httpx.TransportError as exc:
            return _transport_error(exc)
        finally:
            response.close()
        return make_status_error(response.status_code, response.text, response.headers)

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, path, **kwargs).json()

    def _stream(
        self, payload: dict[str, Any], timeout: float | httpx.Timeout | None, max_retries: int | None
    ) -> Generator[StreamChunk, None, None]:
        # A stream is retried only before its body starts: once a byte has arrived the
        # work is under way, and a retry would run and bill it twice.
        response = self._request(
            "POST", "/api/v1/verify", json=payload, stream=True, timeout=timeout, max_retries=max_retries
        )
        request_id = response.headers.get("request-id")
        try:
            for data in iter_sse(_iter_lines(response)):
                chunk = parse_chunk(data)
                if chunk is not None:
                    yield _with_request_id(chunk, request_id)
        finally:
            response.close()

    def health(self, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None) -> dict[str, Any]:
        """Check that the API is up. Needs no API key."""
        return self._json("GET", "/health", auth=False, timeout=timeout, max_retries=max_retries)

    def usage(self, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None) -> Usage:
        """Return usage for the calling API key.

        Request counts (current window, today, this month), rate-limit headroom,
        verdict distribution and latency percentiles, scoped strictly to the calling
        key, plus each product's plan status and allowance for the account.
        """
        data = self._json("GET", "/api/v1/usage", timeout=timeout, max_retries=max_retries)
        return Usage.model_validate(data)

    def verify_stream(
        self,
        claim: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        post_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> Iterator[StreamChunk]:
        """Fact-check one claim, yielding typed progress chunks as they arrive.

        The stream ends with a ``DoneChunk`` carrying the result, or an
        ``ErrorChunk``. ``post_url`` names the social post the claim came from; when
        Titrate is enabled for the account the stream also carries a ``TitrateChunk``.
        """
        return self._stream(_claim_payload(claim, language, priority, post_url), timeout, max_retries)

    def verify(
        self,
        claim: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        post_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> VerificationResult:
        """Fact-check one claim and return the verdict, reasoning and citations.

        Raises:
            VerificationError: the check reported an error instead of a result.
            CantiviaError: the stream ended without a result.
        """
        chunks = self._stream(_claim_payload(claim, language, priority, post_url), timeout, max_retries)
        return _final(chunks, DoneChunk, "verification")

    def verify_article_stream(
        self,
        article: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> Iterator[StreamChunk]:
        """Fact-check a document (article, transcript, post), yielding chunks as they arrive.

        The text is split into check-worthy claims and each is verified on its own:
        the stream carries ``claims_extracted``, then a ``claim_verified`` per claim,
        and ends in ``article_done``. ``article`` is the text itself; the API does
        not fetch URLs, so a link is checked as a link, not as the page behind it.
        """
        return self._stream(_article_payload(article, language, priority), timeout, max_retries)

    def verify_article(
        self,
        article: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> ArticleVerificationResult:
        """Fact-check a document and return every claim's verdict plus an overall verdict.

        Raises:
            VerificationError: the check reported an error, including a failure to
                extract claims.
            CantiviaError: the stream ended without a result.
        """
        chunks = self._stream(_article_payload(article, language, priority), timeout, max_retries)
        return _final(chunks, ArticleDoneChunk, "article verification")

    def verify_image_stream(
        self,
        image: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        forensics_only: bool = False,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> Iterator[StreamChunk]:
        """Fact-check the text in an image (screenshot, meme, infographic), yielding chunks.

        The image's text is read and checked as a document, so the stream carries
        ``ocr_complete`` and then the article chunks, ending in ``article_done``.
        With ``forensics_only`` only media forensics runs and the stream is
        ``media_forensics`` then ``media_done``. ``image`` is base64; a ``data:`` URI
        prefix is accepted.
        """
        return self._stream(_image_payload(image, language, priority, forensics_only), timeout, max_retries)

    def verify_image(
        self,
        image: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> ArticleVerificationResult:
        """Fact-check the text in an image and return the document result.

        Raises:
            VerificationError: the check reported an error, including an image whose
                text could not be read.
            CantiviaError: the stream ended without a result.
        """
        chunks = self._stream(_image_payload(image, language, priority, False), timeout, max_retries)
        return _final(chunks, ArticleDoneChunk, "image verification")

    def verify_media_stream(
        self,
        media: str,
        modality: Modality,
        *,
        mime: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> Iterator[StreamChunk]:
        """Run media forensics on an image, audio clip or video, yielding chunks.

        The file is checked for C2PA provenance and matched against the account's
        enrolled catalogue. No claim is checked: the stream is one
        ``media_forensics`` chunk and ``media_done``. ``media`` is base64.
        """
        return self._stream(_media_payload(media, modality, mime), timeout, max_retries)

    def verify_media(
        self,
        media: str,
        modality: Modality,
        *,
        mime: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> MediaForensicsResult:
        """Run media forensics on an image, audio clip or video and return the result.

        The verdict is ``synthetic`` or ``unknown``, never "real". Show each
        channel's ``detail`` and ``caveats`` with it, and read
        ``unavailable`` for channels that could not run.

        Raises:
            VerificationError: the check reported an error.
            CantiviaError: the stream ended without a result.
        """
        chunks = self._stream(_media_payload(media, modality, mime), timeout, max_retries)
        return _final(chunks, MediaForensicsChunk, f"{modality} verification")

    def enroll_media(
        self,
        assets: list[CatalogueAsset] | list[dict[str, str]],
        *,
        source: str = "api",
        max_wait: float = ENROLL_MAX_WAIT,
        poll_interval: float = ENROLL_POLL_INTERVAL,
        on_progress: Callable[[CatalogueEnrollJob], None] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> CatalogueEnrollJob:
        """Enrol media you hold the rights to into the account's catalogue, and wait.

        Later image, audio and video checks with any of the account's keys match
        copies of it; no other account sees it. Submits the assets, then reads the
        job every ``poll_interval`` seconds until it is ``done`` or ``failed``,
        calling ``on_progress`` with every snapshot, the first included. ``done`` can
        still carry failed assets: read each asset's ``status`` and ``reason``. A job
        unfinished after ``max_wait`` seconds raises ``CantiviaError`` naming its id;
        it keeps running, and ``get_enrollment_job`` reads it on.
        """
        deadline = time.monotonic() + max_wait
        job = self.start_enrollment(assets, source=source, timeout=timeout, max_retries=max_retries)
        while True:
            if on_progress is not None:
                on_progress(job)
            if job.status in ("done", "failed"):
                return job
            if time.monotonic() >= deadline:
                raise _enroll_timed_out(job, max_wait)
            _sleep(poll_interval)
            job = self.get_enrollment_job(job.job_id, timeout=timeout, max_retries=max_retries)

    def start_enrollment(
        self,
        assets: list[CatalogueAsset] | list[dict[str, str]],
        *,
        source: str = "api",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> CatalogueEnrollJob:
        """Submit assets for enrolment and return the job at once, without waiting."""
        data = self._json(
            "POST",
            "/api/v1/catalogue/assets",
            json=_enroll_payload(assets, source),
            timeout=timeout,
            max_retries=max_retries,
        )
        return CatalogueEnrollJob.model_validate(data)

    def get_enrollment_job(
        self, job_id: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> CatalogueEnrollJob:
        """Read an enrolment job's progress.

        ``job_id`` is the UUID the enrolment answered with; anything else raises
        ``ValueError`` before a request is sent. A job expires an hour after it
        finishes and then raises ``NotFoundError``. Resubmitting the same assets
        enrols and bills nothing twice.
        """
        data = self._json("GET", _job_path(job_id), timeout=timeout, max_retries=max_retries)
        return CatalogueEnrollJob.model_validate(data)

    def list_catalogue(
        self, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> CatalogueListResponse:
        """List what the account's catalogue holds, with its size and cap.

        Each asset's id (the SHA-256 of its bytes), name, modality and enrolment
        time. Metadata only: the catalogue never keeps the uploaded bytes.
        """
        data = self._json("GET", "/api/v1/catalogue/assets", timeout=timeout, max_retries=max_retries)
        return CatalogueListResponse.model_validate(data)

    def delete_catalogue_asset(
        self, asset_id: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> CatalogueDeleteResponse:
        """Erase one asset from the account's catalogue.

        ``asset_id`` is the id ``list_catalogue`` reports; anything else raises
        ``ValueError`` before a request is sent. An asset the catalogue does not
        hold raises ``NotFoundError``.
        """
        data = self._json("DELETE", _asset_path(asset_id), timeout=timeout, max_retries=max_retries)
        return CatalogueDeleteResponse.model_validate(data)

    def draft_notice(
        self,
        name: str,
        media: str,
        *,
        infringing_url: str,
        complainant: Complainant | dict[str, str],
        host: str = "",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> CatalogueNoticeResponse:
        """Draft a takedown notice for a copy of catalogued media found online.

        Drafted only if the copy matches the account's catalogue, and only as a
        draft: ``status`` READY_FOR_REVIEW means a named person must check and
        approve it before anyone sends it. Nothing is sent. A copy that does not
        match is UNKNOWN.
        """
        data = self._json(
            "POST",
            "/api/v1/catalogue/notice",
            json=_notice_payload(name, media, infringing_url, complainant, host),
            timeout=timeout,
            max_retries=max_retries,
        )
        return CatalogueNoticeResponse.model_validate(data)

    def score_post_stream(
        self, post_url: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> Iterator[StreamChunk]:
        """Score how a social post is spreading (Titrate), yielding chunks as they arrive.

        No fact-check runs. The stream carries one ``titrate`` chunk, or an
        ``error`` when Titrate is not enabled.
        """
        return self._stream(_post_payload(post_url), timeout, max_retries)

    def score_post(
        self, post_url: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> Titrate:
        """Score how a social post is spreading (Titrate) and return the answer.

        The answer is often ``scored=False``: most posts get no replies, and a post
        under an hour old has not finished its window. Read ``abstain_reason``.

        Raises:
            VerificationError: the API reported an error, including Titrate not
                being enabled.
            CantiviaError: the stream ended without an answer.
        """
        chunks = self._stream(_post_payload(post_url), timeout, max_retries)
        return _final(chunks, TitrateChunk, "post scoring")

    def resolve(
        self,
        follow_up: str,
        *,
        prior_claims: list[str] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> ResolveResult:
        """Rewrite a conversational follow-up into a standalone claim to verify.

        "What about last year?" asserts nothing on its own; against the earlier
        claims in ``prior_claims`` it becomes something checkable. Pass the claims,
        never their verdicts. When ``resolved`` is false, ``claim`` is the follow-up
        unchanged. Not billed as a verification.
        """
        data = self._json(
            "POST",
            "/api/v1/resolve",
            json=_resolve_payload(follow_up, prior_claims),
            timeout=timeout,
            max_retries=max_retries,
        )
        return ResolveResult.model_validate(data)

    def screen_manuscript(
        self,
        title: str = "",
        abstract: str = "",
        *,
        budget: float | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> PaperMillScreenResult:
        """Screen a manuscript's title and abstract for paper-mill writing style.

        Returns ``review`` (a person should read it) or ``pass``, with the precision
        measured at the flag rate it cleared and the terms behind the score. It
        scores style, not fraud. ``budget`` is the flag rate your reviewers can
        staff, e.g. 0.01. At least one of ``title`` and ``abstract`` must hold text;
        an empty manuscript raises ``ValueError`` before a request is sent.
        """
        data = self._json(
            "POST",
            "/api/v1/paper-mill/screen",
            json=_screen_payload(title, abstract, budget),
            timeout=timeout,
            max_retries=max_retries,
        )
        return PaperMillScreenResult.model_validate(data)

    def submit_batch(
        self,
        claims: list[str],
        *,
        kind: Literal["claim", "article"] = "claim",
        idempotency_key: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> BatchAccepted:
        """Submit up to 500 claims (or articles) to verify in the background.

        Returns at once with the batch id; ``get_batch`` reads progress and
        ``wait_for_batch`` waits for the end. Each item is billed as it is
        verified. Send an ``idempotency_key`` to make resubmitting safe: the same
        key returns the batch it first created instead of starting another.
        """
        data = self._json(
            "POST",
            "/api/v1/batch",
            json=_batch_payload(claims, kind),
            headers=_batch_headers(idempotency_key),
            timeout=timeout,
            max_retries=max_retries,
        )
        return BatchAccepted.model_validate(data)

    def get_batch(
        self, batch_id: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> Batch:
        """Read a batch's progress and the results of the items finished so far.

        ``batch_id`` is the UUID ``submit_batch`` answered with; anything else
        raises ``ValueError`` before a request is sent. A batch submitted with
        another key raises ``NotFoundError``.
        """
        data = self._json("GET", _batch_path(batch_id), timeout=timeout, max_retries=max_retries)
        return Batch.model_validate(data)

    def wait_for_batch(
        self,
        batch_id: str,
        *,
        max_wait: float = BATCH_MAX_WAIT,
        poll_interval: float = BATCH_POLL_INTERVAL,
        on_progress: Callable[[Batch], None] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> Batch:
        """Wait for a batch to finish and return it.

        Reads the batch every ``poll_interval`` seconds until it is ``completed`` or
        ``failed``, calling ``on_progress`` with every snapshot, the first included.
        A completed batch can still hold failed items. A batch unfinished after
        ``max_wait`` seconds raises ``CantiviaError`` naming its id; it keeps
        running, and ``get_batch`` reads it on.
        """
        deadline = time.monotonic() + max_wait
        while True:
            batch = self.get_batch(batch_id, timeout=timeout, max_retries=max_retries)
            if on_progress is not None:
                on_progress(batch)
            if batch.status in ("completed", "failed"):
                return batch
            if time.monotonic() >= deadline:
                raise _batch_timed_out(batch, max_wait)
            _sleep(poll_interval)


class AsyncCantiviaClient(_BaseClient):
    """Asynchronous client for the Cantivia API.

    Takes the same arguments as ``CantiviaClient`` (``http_client`` is an
    ``httpx.AsyncClient`` here) and has the same methods, as coroutines; the
    ``*_stream`` methods are async iterators. Use it as an async context manager,
    or call ``aclose()``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = http_client if http_client is not None else httpx.AsyncClient()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connections."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
        stream: bool = False,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        retries = self._retries_for(max_retries)
        attempt = 0
        while True:
            request = self._client.build_request(
                method,
                self.base_url + path,
                json=json,
                headers=self._headers(auth, headers),
                timeout=self._timeout_for(timeout),
            )
            try:
                response = await self._client.send(request, stream=stream)
            except httpx.TransportError as exc:
                if is_retryable_exception(exc) and attempt < retries:
                    await _asleep(retry_delay(attempt, None))
                    attempt += 1
                    continue
                raise _transport_error(exc) from exc
            if response.status_code < 400:
                return response
            if is_retryable_status(response.status_code) and attempt < retries:
                await response.aclose()
                await _asleep(retry_delay(attempt, parse_retry_after(response.headers.get("retry-after"))))
                attempt += 1
                continue
            raise await self._status_error(response)

    async def _status_error(self, response: httpx.Response) -> APIStatusError | APIConnectionError:
        try:
            await response.aread()
        except httpx.TransportError as exc:
            return _transport_error(exc)
        finally:
            await response.aclose()
        return make_status_error(response.status_code, response.text, response.headers)

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return (await self._request(method, path, **kwargs)).json()

    async def _stream(
        self, payload: dict[str, Any], timeout: float | httpx.Timeout | None, max_retries: int | None
    ) -> AsyncGenerator[StreamChunk, None]:
        # Retried only before the body starts; see CantiviaClient._stream.
        response = await self._request(
            "POST", "/api/v1/verify", json=payload, stream=True, timeout=timeout, max_retries=max_retries
        )
        request_id = response.headers.get("request-id")
        try:
            async for data in aiter_sse(_aiter_lines(response)):
                chunk = parse_chunk(data)
                if chunk is not None:
                    yield _with_request_id(chunk, request_id)
        finally:
            await response.aclose()

    async def health(
        self, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> dict[str, Any]:
        """Check that the API is up. Needs no API key."""
        return await self._json("GET", "/health", auth=False, timeout=timeout, max_retries=max_retries)

    async def usage(self, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None) -> Usage:
        """Return usage for the calling API key. See ``CantiviaClient.usage``."""
        data = await self._json("GET", "/api/v1/usage", timeout=timeout, max_retries=max_retries)
        return Usage.model_validate(data)

    def verify_stream(
        self,
        claim: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        post_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Fact-check one claim, yielding chunks as they arrive. See ``CantiviaClient.verify_stream``."""
        return self._stream(_claim_payload(claim, language, priority, post_url), timeout, max_retries)

    async def verify(
        self,
        claim: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        post_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> VerificationResult:
        """Fact-check one claim and return the result. See ``CantiviaClient.verify``."""
        chunks = self._stream(_claim_payload(claim, language, priority, post_url), timeout, max_retries)
        return await _afinal(chunks, DoneChunk, "verification")

    def verify_article_stream(
        self,
        article: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Fact-check a document, yielding chunks. See ``CantiviaClient.verify_article_stream``."""
        return self._stream(_article_payload(article, language, priority), timeout, max_retries)

    async def verify_article(
        self,
        article: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> ArticleVerificationResult:
        """Fact-check a document and return the result. See ``CantiviaClient.verify_article``."""
        chunks = self._stream(_article_payload(article, language, priority), timeout, max_retries)
        return await _afinal(chunks, ArticleDoneChunk, "article verification")

    def verify_image_stream(
        self,
        image: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        forensics_only: bool = False,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Fact-check the text in an image, yielding chunks. See ``CantiviaClient.verify_image_stream``."""
        return self._stream(_image_payload(image, language, priority, forensics_only), timeout, max_retries)

    async def verify_image(
        self,
        image: str,
        *,
        language: str = "en",
        priority: Priority = "standard",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> ArticleVerificationResult:
        """Fact-check the text in an image and return the result. See ``CantiviaClient.verify_image``."""
        chunks = self._stream(_image_payload(image, language, priority, False), timeout, max_retries)
        return await _afinal(chunks, ArticleDoneChunk, "image verification")

    def verify_media_stream(
        self,
        media: str,
        modality: Modality,
        *,
        mime: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Run media forensics, yielding chunks. See ``CantiviaClient.verify_media_stream``."""
        return self._stream(_media_payload(media, modality, mime), timeout, max_retries)

    async def verify_media(
        self,
        media: str,
        modality: Modality,
        *,
        mime: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> MediaForensicsResult:
        """Run media forensics and return the result. See ``CantiviaClient.verify_media``."""
        chunks = self._stream(_media_payload(media, modality, mime), timeout, max_retries)
        return await _afinal(chunks, MediaForensicsChunk, f"{modality} verification")

    async def enroll_media(
        self,
        assets: list[CatalogueAsset] | list[dict[str, str]],
        *,
        source: str = "api",
        max_wait: float = ENROLL_MAX_WAIT,
        poll_interval: float = ENROLL_POLL_INTERVAL,
        on_progress: Callable[[CatalogueEnrollJob], None] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> CatalogueEnrollJob:
        """Enrol media into the account's catalogue, and wait. See ``CantiviaClient.enroll_media``."""
        deadline = time.monotonic() + max_wait
        job = await self.start_enrollment(assets, source=source, timeout=timeout, max_retries=max_retries)
        while True:
            if on_progress is not None:
                on_progress(job)
            if job.status in ("done", "failed"):
                return job
            if time.monotonic() >= deadline:
                raise _enroll_timed_out(job, max_wait)
            await _asleep(poll_interval)
            job = await self.get_enrollment_job(job.job_id, timeout=timeout, max_retries=max_retries)

    async def start_enrollment(
        self,
        assets: list[CatalogueAsset] | list[dict[str, str]],
        *,
        source: str = "api",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> CatalogueEnrollJob:
        """Submit assets for enrolment without waiting. See ``CantiviaClient.start_enrollment``."""
        data = await self._json(
            "POST",
            "/api/v1/catalogue/assets",
            json=_enroll_payload(assets, source),
            timeout=timeout,
            max_retries=max_retries,
        )
        return CatalogueEnrollJob.model_validate(data)

    async def get_enrollment_job(
        self, job_id: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> CatalogueEnrollJob:
        """Read an enrolment job's progress. See ``CantiviaClient.get_enrollment_job``."""
        data = await self._json("GET", _job_path(job_id), timeout=timeout, max_retries=max_retries)
        return CatalogueEnrollJob.model_validate(data)

    async def list_catalogue(
        self, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> CatalogueListResponse:
        """List what the account's catalogue holds. See ``CantiviaClient.list_catalogue``."""
        data = await self._json("GET", "/api/v1/catalogue/assets", timeout=timeout, max_retries=max_retries)
        return CatalogueListResponse.model_validate(data)

    async def delete_catalogue_asset(
        self, asset_id: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> CatalogueDeleteResponse:
        """Erase one asset from the account's catalogue. See ``CantiviaClient.delete_catalogue_asset``."""
        data = await self._json("DELETE", _asset_path(asset_id), timeout=timeout, max_retries=max_retries)
        return CatalogueDeleteResponse.model_validate(data)

    async def draft_notice(
        self,
        name: str,
        media: str,
        *,
        infringing_url: str,
        complainant: Complainant | dict[str, str],
        host: str = "",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> CatalogueNoticeResponse:
        """Draft a takedown notice for review. See ``CantiviaClient.draft_notice``."""
        data = await self._json(
            "POST",
            "/api/v1/catalogue/notice",
            json=_notice_payload(name, media, infringing_url, complainant, host),
            timeout=timeout,
            max_retries=max_retries,
        )
        return CatalogueNoticeResponse.model_validate(data)

    def score_post_stream(
        self, post_url: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> AsyncIterator[StreamChunk]:
        """Score how a social post is spreading, yielding chunks. See ``CantiviaClient.score_post_stream``."""
        return self._stream(_post_payload(post_url), timeout, max_retries)

    async def score_post(
        self, post_url: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> Titrate:
        """Score how a social post is spreading. See ``CantiviaClient.score_post``."""
        chunks = self._stream(_post_payload(post_url), timeout, max_retries)
        return await _afinal(chunks, TitrateChunk, "post scoring")

    async def resolve(
        self,
        follow_up: str,
        *,
        prior_claims: list[str] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> ResolveResult:
        """Rewrite a follow-up into a standalone claim. See ``CantiviaClient.resolve``."""
        data = await self._json(
            "POST",
            "/api/v1/resolve",
            json=_resolve_payload(follow_up, prior_claims),
            timeout=timeout,
            max_retries=max_retries,
        )
        return ResolveResult.model_validate(data)

    async def screen_manuscript(
        self,
        title: str = "",
        abstract: str = "",
        *,
        budget: float | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> PaperMillScreenResult:
        """Screen a manuscript for paper-mill style. See ``CantiviaClient.screen_manuscript``."""
        data = await self._json(
            "POST",
            "/api/v1/paper-mill/screen",
            json=_screen_payload(title, abstract, budget),
            timeout=timeout,
            max_retries=max_retries,
        )
        return PaperMillScreenResult.model_validate(data)

    async def submit_batch(
        self,
        claims: list[str],
        *,
        kind: Literal["claim", "article"] = "claim",
        idempotency_key: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> BatchAccepted:
        """Submit claims to verify in the background. See ``CantiviaClient.submit_batch``."""
        data = await self._json(
            "POST",
            "/api/v1/batch",
            json=_batch_payload(claims, kind),
            headers=_batch_headers(idempotency_key),
            timeout=timeout,
            max_retries=max_retries,
        )
        return BatchAccepted.model_validate(data)

    async def get_batch(
        self, batch_id: str, *, timeout: float | httpx.Timeout | None = None, max_retries: int | None = None
    ) -> Batch:
        """Read a batch's progress and results. See ``CantiviaClient.get_batch``."""
        data = await self._json("GET", _batch_path(batch_id), timeout=timeout, max_retries=max_retries)
        return Batch.model_validate(data)

    async def wait_for_batch(
        self,
        batch_id: str,
        *,
        max_wait: float = BATCH_MAX_WAIT,
        poll_interval: float = BATCH_POLL_INTERVAL,
        on_progress: Callable[[Batch], None] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> Batch:
        """Wait for a batch to finish and return it. See ``CantiviaClient.wait_for_batch``."""
        deadline = time.monotonic() + max_wait
        while True:
            batch = await self.get_batch(batch_id, timeout=timeout, max_retries=max_retries)
            if on_progress is not None:
                on_progress(batch)
            if batch.status in ("completed", "failed"):
                return batch
            if time.monotonic() >= deadline:
                raise _batch_timed_out(batch, max_wait)
            await _asleep(poll_interval)
