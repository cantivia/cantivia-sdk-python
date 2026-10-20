"""HTTP-path tests using httpx.MockTransport (no live API needed).

The async paths are driven with `asyncio.run()` rather than a pytest plugin, so
the whole suite runs under a bare `python -m pytest` with no `pytest-asyncio`.
"""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from cantivia import (
    MAX_CLAIM_CHARS,
    ArticleVerificationResult,
    AsyncCantiviaClient,
    AuthenticationError,
    CantiviaClient,
    CantiviaError,
    CatalogueDeleteResponse,
    CatalogueEnrollJob,
    CatalogueListResponse,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    Usage,
    Verdict,
    VerificationError,
    VerificationResult,
)

USAGE_JSON = {
    "key_id": "abc123",
    "requests": {"window": 3, "today": 42, "month": 1200},
    "rate_limit": {"limit": 100, "remaining": 97, "window_seconds": 60},
    "verdicts": {"true": 10, "fake": 5},
    "latency_ms": {"p50": 820.0, "p95": 1400.0, "samples": 200},
}

VERIFY_SSE = (
    'data: {"type":"routing","action":"route-a","difficulty":0.7}\n\n'
    'data: {"type":"factcheck_complete","verdict":"fake","confidence":0.97}\n\n'
    'data: {"type":"done","result":{"claim":"The Eiffel Tower is in Berlin.",'
    '"verdict":"fake","confidence":0.97,"reasoning":"It is in Paris.",'
    '"citations":[{"url":"https://en.wikipedia.org/wiki/Eiffel_Tower","quote":"in Paris"}],'
    '"cacheHit":false,"route":"route-a","processingMs":850}}\n\n'
)

ERROR_SSE = 'data: {"type":"error","message":"the check timed out"}\n\n'

# Image path: ocr_complete -> claims_extracted -> claim_verified -> article_done.
_CLAIM_RESULT = (
    '{"claim":"Vaccines cause autism","verdict":"fake","confidence":0.96,'
    '"reasoning":"No credible evidence.","citations":[],'
    '"cacheHit":false,"route":"route-a","processingMs":900}'
)
IMAGE_SSE = (
    'data: {"type":"ocr_complete","textChars":21,"language":"en"}\n\n'
    'data: {"type":"claims_extracted","count":1,"method":"heuristic","claims":['
    '{"claim":"Vaccines cause autism","checkWorthiness":0.9,"claimType":"causal",'
    '"originalText":"Vaccines cause autism","entities":[],"verifiable":true}]}\n\n'
    f'data: {{"type":"claim_verified","index":0,"total":1,"result":{_CLAIM_RESULT}}}\n\n'
    'data: {"type":"article_done","result":{"claimCount":1,"verifiedCount":1,"degradedCount":0,"overallVerdict":"fake",'
    '"overallConfidence":0.9,"summary":"One false claim.","extractionMethod":"heuristic",'
    f'"claims":[{_CLAIM_RESULT}],"processingMs":1200}}}}\n\n'
)
OCR_UNAVAILABLE_SSE = 'data: {"type":"error","message":"OCR backend unavailable"}\n\n'

# Article path: the image path without the OCR hop.
ARTICLE_SSE = (
    'data: {"type":"claims_extracted","count":1,"method":"model","claims":['
    '{"claim":"Vaccines cause autism","checkWorthiness":0.9,"claimType":"causal",'
    '"originalText":"Vaccines cause autism","entities":[],"verifiable":true}]}\n\n'
    f'data: {{"type":"claim_verified","index":0,"total":1,"result":{_CLAIM_RESULT}}}\n\n'
    'data: {"type":"article_done","result":{"claimCount":1,"verifiedCount":1,"degradedCount":0,"overallVerdict":"fake",'
    '"overallConfidence":0.9,"summary":"One false claim.","extractionMethod":"model",'
    f'"claims":[{_CLAIM_RESULT}],"processingMs":4200}}}}\n\n'
)

_ARTICLE_TEXT = "RTE reported that vaccines cause autism. Health officials disagreed."

_TINY_IMAGE_B64 = "iVBORw0KGgo="  # not decoded client-side; just a non-empty payload


def _handler(
    verify_body: str = VERIFY_SSE,
    status: int = 200,
    error_headers: dict | None = None,
):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if req.url.path == "/api/v1/usage":
            if status >= 400:
                return httpx.Response(status, text="denied", headers=error_headers or {})
            return httpx.Response(200, json=USAGE_JSON)
        if req.url.path == "/api/v1/verify":
            if status >= 400:
                return httpx.Response(status, text="denied", headers=error_headers or {})
            return httpx.Response(200, text=verify_body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404, text="not found")

    return handler


def _sync_client(handler, api_key="ctv_test"):
    # No retries here: these tests are about one response each. test_retries.py covers retrying.
    inner = httpx.Client(transport=httpx.MockTransport(handler))
    return CantiviaClient(api_key=api_key, base_url="http://gw", max_retries=0, http_client=inner)


def _async_client(handler, api_key="ctv_test"):
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncCantiviaClient(api_key=api_key, base_url="http://gw", max_retries=0, http_client=inner)


def test_sync_verify_returns_result():
    with _sync_client(_handler()) as client:
        result = client.verify("The Eiffel Tower is in Berlin.")
    assert isinstance(result, VerificationResult)
    assert result.verdict is Verdict.FAKE
    assert result.confidence == 0.97
    assert result.cache_hit is False
    assert result.processing_ms == 850
    assert result.citations[0].url == "https://en.wikipedia.org/wiki/Eiffel_Tower"


def test_sync_verify_stream_yields_typed_chunks():
    with _sync_client(_handler()) as client:
        types = [chunk.type for chunk in client.verify_stream("x")]
    assert types == ["routing", "factcheck_complete", "done"]


# Event types this release does not know, as a newer API might send them.
UNKNOWN_EVENTS_SSE = (
    'data: {"type":"event_from_a_newer_api","detail":"x"}\n\n' + VERIFY_SSE + 'data: {"type":"another_new_event"}\n\n'
)


def test_stream_skips_event_types_it_does_not_know():
    with _sync_client(_handler(UNKNOWN_EVENTS_SSE)) as client:
        types = [chunk.type for chunk in client.verify_stream("x")]
        result = client.verify("x")
    assert types == ["routing", "factcheck_complete", "done"]
    assert result.verdict is Verdict.FAKE


def test_async_stream_skips_event_types_it_does_not_know():
    async def run():
        async with _async_client(_handler(UNKNOWN_EVENTS_SSE)) as client:
            types = [chunk.type async for chunk in client.verify_stream("x")]
            return types, await client.verify("x")

    types, result = asyncio.run(run())
    assert types == ["routing", "factcheck_complete", "done"]
    assert result.verdict is Verdict.FAKE


def test_sync_health():
    with _sync_client(_handler()) as client:
        assert client.health() == {"status": "ok"}


def test_auth_header_is_sent():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=USAGE_JSON)

    with _sync_client(handler) as client:
        client.usage()
    assert seen["auth"] == "Bearer ctv_test"


def test_verification_error_on_error_chunk():
    with _sync_client(_handler(verify_body=ERROR_SSE)) as client, pytest.raises(VerificationError, match="timed out"):
        client.verify("x")


def test_401_maps_to_authentication_error():
    with _sync_client(_handler(status=401)) as client, pytest.raises(AuthenticationError) as exc:
        client.verify("x")
    assert exc.value.status_code == 401


def test_429_maps_to_rate_limit_error():
    with _sync_client(_handler(status=429)) as client, pytest.raises(RateLimitError):
        client.verify("x")


def test_500_maps_to_internal_server_error():
    with _sync_client(_handler(status=500)) as client, pytest.raises(InternalServerError) as exc:
        client.verify("x")
    assert exc.value.status_code == 500


def test_403_maps_to_permission_denied_error():
    with _sync_client(_handler(status=403)) as client, pytest.raises(PermissionDeniedError) as exc:
        client.verify("x")
    assert exc.value.status_code == 403


def test_429_includes_retry_after_header():
    handler = _handler(status=429, error_headers={"Retry-After": "12"})
    with _sync_client(handler) as client, pytest.raises(RateLimitError) as exc:
        client.verify("x")
    assert exc.value.retry_after == 12


def test_429_without_retry_after_has_none():
    with _sync_client(_handler(status=429)) as client, pytest.raises(RateLimitError) as exc:
        client.verify("x")
    assert exc.value.retry_after is None


def test_sync_usage_returns_typed_model():
    with _sync_client(_handler()) as client:
        usage = client.usage()
    assert isinstance(usage, Usage)
    assert usage.key_id == "abc123"
    assert usage.requests.month == 1200
    assert usage.rate_limit.remaining == 97
    assert usage.rate_limit.window_seconds == 60
    assert usage.verdicts == {"true": 10, "fake": 5}
    assert usage.latency_ms.p95 == 1400.0
    assert usage.latency_ms.samples == 200


def test_usage_parses_per_product_entitlements():
    payload = {
        **USAGE_JSON,
        "products": [
            {
                "product": "titrate",
                "status": "pilot",
                "tier": "pilot",
                "pilot_ends_at": "2026-10-14T09:00:00+00:00",
                "units": [{"unit": "post_scored", "used": 40, "allowance": 100_000, "overage": 0}],
            },
            {
                "product": "factcheck",
                "status": "none",
                "tier": None,
                "pilot_ends_at": None,
                "units": [{"unit": "claim", "used": 0, "allowance": None, "overage": 0}],
            },
        ],
    }
    titrate, factcheck = Usage.model_validate(payload).products
    assert titrate.product == "titrate"
    assert titrate.status == "pilot"
    assert titrate.units[0].allowance == 100_000
    assert factcheck.tier is None
    assert factcheck.units[0].allowance is None
    # A usage report without products reads as an empty list.
    assert Usage.model_validate(USAGE_JSON).products == []


def test_usage_401_maps_to_authentication_error():
    with _sync_client(_handler(status=401)) as client, pytest.raises(AuthenticationError):
        client.usage()


def test_async_usage_returns_typed_model():
    async def run():
        client = _async_client(_handler())
        try:
            return await client.usage()
        finally:
            await client.aclose()

    usage = asyncio.run(run())
    assert usage.requests.window == 3
    assert usage.verdicts["true"] == 10


def test_a_supplied_http_client_still_gets_the_client_settings():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=USAGE_JSON)

    inner = httpx.Client(base_url="http://ignored", transport=httpx.MockTransport(handler))
    with CantiviaClient(api_key="ctv_test", base_url="http://gw/", http_client=inner) as client:
        client.usage()
    assert seen == {"url": "http://gw/api/v1/usage", "auth": "Bearer ctv_test"}


def test_async_verify_returns_result():
    async def run():
        client = _async_client(_handler())
        try:
            return await client.verify("x")
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result.verdict is Verdict.FAKE


def test_async_verify_stream_yields_typed_chunks():
    async def run():
        client = _async_client(_handler())
        try:
            return [chunk.type async for chunk in client.verify_stream("x")]
        finally:
            await client.aclose()

    types = asyncio.run(run())
    assert types == ["routing", "factcheck_complete", "done"]


def test_sync_verify_image_returns_article_result():
    with _sync_client(_handler(verify_body=IMAGE_SSE)) as client:
        result = client.verify_image(_TINY_IMAGE_B64)
    assert isinstance(result, ArticleVerificationResult)
    assert result.overall_verdict is Verdict.FAKE
    assert result.claim_count == 1
    assert result.claims[0].verdict is Verdict.FAKE
    assert result.processing_ms == 1200


def test_sync_verify_image_stream_starts_with_ocr_complete():
    with _sync_client(_handler(verify_body=IMAGE_SSE)) as client:
        chunks = list(client.verify_image_stream(_TINY_IMAGE_B64))
    types = [c.type for c in chunks]
    assert types == ["ocr_complete", "claims_extracted", "claim_verified", "article_done"]
    assert chunks[0].text_chars == 21


def test_verify_image_raises_when_ocr_unavailable():
    with (
        _sync_client(_handler(verify_body=OCR_UNAVAILABLE_SSE)) as client,
        pytest.raises(VerificationError, match="OCR backend unavailable"),
    ):
        client.verify_image(_TINY_IMAGE_B64)


def test_verify_image_rejects_empty_image():
    # ImageVerificationRequest pins image to min_length=1, so this never reaches
    # the transport: the request model rejects it client-side.
    with (
        _sync_client(_handler(verify_body=IMAGE_SSE)) as client,
        pytest.raises(ValidationError),
    ):
        client.verify_image("")


def test_async_verify_image_returns_article_result():
    async def run():
        client = _async_client(_handler(verify_body=IMAGE_SSE))
        try:
            return await client.verify_image(_TINY_IMAGE_B64)
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert isinstance(result, ArticleVerificationResult)
    assert result.overall_verdict is Verdict.FAKE


def test_async_context_manager_closes_client():
    inner = httpx.AsyncClient(transport=httpx.MockTransport(_handler()))

    async def run():
        # health() needs no API key.
        async with AsyncCantiviaClient(base_url="http://gw", http_client=inner) as client:
            assert await client.health() == {"status": "ok"}

    asyncio.run(run())
    assert inner.is_closed


def _recording_handler(verify_body: str):
    """Handler that keeps the last /api/v1/verify request body for assertions."""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/verify":
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, text=verify_body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404, text="not found")

    return handler, seen


def test_sync_verify_article_returns_article_result():
    with _sync_client(_handler(verify_body=ARTICLE_SSE)) as client:
        result = client.verify_article(_ARTICLE_TEXT)
    assert isinstance(result, ArticleVerificationResult)
    assert result.overall_verdict is Verdict.FAKE
    assert result.claim_count == 1
    assert result.processing_ms == 4200


def test_sync_verify_article_stream_has_no_ocr_hop():
    with _sync_client(_handler(verify_body=ARTICLE_SSE)) as client:
        types = [c.type for c in client.verify_article_stream(_ARTICLE_TEXT)]
    assert types == ["claims_extracted", "claim_verified", "article_done"]


def test_verify_article_sends_kind_article():
    """The whole point of the request model: without `kind` the server runs single-claim mode."""
    handler, seen = _recording_handler(ARTICLE_SSE)
    with _sync_client(handler) as client:
        client.verify_article(_ARTICLE_TEXT)
    assert seen["body"]["kind"] == "article"
    assert seen["body"]["claim"] == _ARTICLE_TEXT


def test_verify_sends_no_kind_so_the_server_default_applies():
    handler, seen = _recording_handler(VERIFY_SSE)
    with _sync_client(handler) as client:
        client.verify("x")
    assert "kind" not in seen["body"]


MEDIA_ONLY_SSE = (
    'data: {"type":"media_forensics","modality":"image","result":{"verdict":"unknown","method":"none",'
    '"combinedP":null,"channels":[],"excised":[],"alpha":0.01,'
    '"unavailable":[{"channel":"retrieval","reason":"nothing enrolled"}]}}\n\n'
    'data: {"type":"media_done","modality":"image"}\n\n'
)


def test_verify_image_sends_forensics_only_false_by_default():
    handler, seen = _recording_handler(IMAGE_SSE)
    with _sync_client(handler) as client:
        client.verify_image(_TINY_IMAGE_B64)
    assert seen["body"]["forensicsOnly"] is False


def test_verify_image_stream_forensics_only_ends_at_media_done():
    handler, seen = _recording_handler(MEDIA_ONLY_SSE)
    with _sync_client(handler) as client:
        types = [c.type for c in client.verify_image_stream(_TINY_IMAGE_B64, forensics_only=True)]
    # The wire name, not the attribute name: an unknown key would be dropped server-side.
    assert seen["body"]["forensicsOnly"] is True
    assert seen["body"]["kind"] == "image"
    assert types == ["media_forensics", "media_done"]


def test_verify_media_on_an_image_asks_for_forensics_only():
    handler, seen = _recording_handler(MEDIA_ONLY_SSE)
    with _sync_client(handler) as client:
        result = client.verify_media(_TINY_IMAGE_B64, "image")
    assert seen["body"]["kind"] == "image"
    assert seen["body"]["image"] == _TINY_IMAGE_B64
    assert seen["body"]["forensicsOnly"] is True
    assert result.unavailable[0].channel == "retrieval"


def test_verify_article_rejects_empty_text():
    with (
        _sync_client(_handler(verify_body=ARTICLE_SSE)) as client,
        pytest.raises(ValidationError),
    ):
        client.verify_article("")


def test_article_body_up_to_the_server_cap_is_accepted():
    """Regression: the client capped `claim` at 10k while the server allows 50k,
    so article bodies the server would have taken were rejected here."""
    body = "x" * MAX_CLAIM_CHARS
    handler, seen = _recording_handler(ARTICLE_SSE)
    with _sync_client(handler) as client:
        client.verify_article(body)
    assert len(seen["body"]["claim"]) == MAX_CLAIM_CHARS

    with (
        _sync_client(_handler(verify_body=ARTICLE_SSE)) as client,
        pytest.raises(ValidationError),
    ):
        client.verify_article("x" * (MAX_CLAIM_CHARS + 1))


def test_async_verify_article_returns_article_result():
    async def run():
        client = _async_client(_handler(verify_body=ARTICLE_SSE))
        try:
            return await client.verify_article(_ARTICLE_TEXT)
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert result.overall_verdict is Verdict.FAKE
    assert result.claim_count == 1


_ASSET_ID = "a" * 64

CATALOGUE_LIST_JSON = {
    "assets": [
        {
            "assetId": _ASSET_ID,
            "name": "press-photo.jpg",
            "modality": "image",
            "enrolledAt": "2026-09-28T10:00:00Z",
            "source": "api",
            "bytesLen": 48213,
        }
    ],
    "gallerySize": 1,
    "maxAssets": 198,
}

CATALOGUE_DELETE_JSON = {"deleted": True, "assetId": _ASSET_ID, "gallerySize": 0, "maxAssets": 198}


def _catalogue_handler(body: dict, status: int = 200):
    """Handler that answers every request with `body` and records method, path and payload."""
    seen: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.content))
        return httpx.Response(status, json=body)

    return handler, seen


def test_sync_list_catalogue_gets_the_assets():
    handler, seen = _catalogue_handler(CATALOGUE_LIST_JSON)
    with _sync_client(handler) as client:
        listing = client.list_catalogue()
    assert seen == [("GET", "/api/v1/catalogue/assets", b"")]
    assert isinstance(listing, CatalogueListResponse)
    record = listing.assets[0]
    assert record.asset_id == _ASSET_ID
    assert record.enrolled_at == "2026-09-28T10:00:00Z"
    assert record.bytes_len == 48213
    assert listing.gallery_size == 1
    assert listing.max_assets == 198


def test_sync_delete_catalogue_asset_deletes_by_id():
    handler, seen = _catalogue_handler(CATALOGUE_DELETE_JSON)
    with _sync_client(handler) as client:
        erased = client.delete_catalogue_asset(_ASSET_ID)
    assert seen == [("DELETE", f"/api/v1/catalogue/assets/{_ASSET_ID}", b"")]
    assert isinstance(erased, CatalogueDeleteResponse)
    assert erased.deleted is True
    assert erased.asset_id == _ASSET_ID
    assert erased.gallery_size == 0


@pytest.mark.parametrize("bad", ["", "a" * 63, "A" * 64, "../" + "a" * 61, "a" * 64 + "/x", "a" * 64 + "\n"])
def test_delete_catalogue_asset_rejects_a_malformed_id_before_sending(bad):
    # The id becomes part of the URL, so anything but 64 lowercase hex never leaves.
    handler, seen = _catalogue_handler(CATALOGUE_DELETE_JSON)
    with _sync_client(handler) as client, pytest.raises(ValueError, match="invalid catalogue asset id"):
        client.delete_catalogue_asset(bad)
    assert seen == []


def test_delete_catalogue_asset_not_held_is_a_404():
    handler, _ = _catalogue_handler({"error": {"code": "not_found", "message": "not held"}}, status=404)
    with _sync_client(handler) as client, pytest.raises(NotFoundError) as exc:
        client.delete_catalogue_asset(_ASSET_ID)
    assert exc.value.status_code == 404


def test_async_list_and_delete_catalogue():
    list_handler, _ = _catalogue_handler(CATALOGUE_LIST_JSON)
    delete_handler, seen = _catalogue_handler(CATALOGUE_DELETE_JSON)

    async def run():
        listing_client = _async_client(list_handler)
        delete_client = _async_client(delete_handler)
        try:
            return await listing_client.list_catalogue(), await delete_client.delete_catalogue_asset(_ASSET_ID)
        finally:
            await listing_client.aclose()
            await delete_client.aclose()

    listing, erased = asyncio.run(run())
    assert listing.assets[0].asset_id == _ASSET_ID
    assert erased.asset_id == _ASSET_ID
    assert seen == [("DELETE", f"/api/v1/catalogue/assets/{_ASSET_ID}", b"")]


def test_async_delete_catalogue_asset_rejects_a_malformed_id():
    handler, seen = _catalogue_handler(CATALOGUE_DELETE_JSON)

    async def run():
        client = _async_client(handler)
        try:
            await client.delete_catalogue_asset("not-an-id")
        finally:
            await client.aclose()

    with pytest.raises(ValueError, match="invalid catalogue asset id"):
        asyncio.run(run())
    assert seen == []


_JOB_ID = "3f2b8c1e-4a5d-4e6f-9a7b-1c2d3e4f5a6b"
_JOB_PATH = f"/api/v1/catalogue/jobs/{_JOB_ID}"
_ASSETS = [{"name": "press-photo.jpg", "media": _TINY_IMAGE_B64}]


def _enroll_job(status: str, asset_status: str, **extra) -> dict:
    return {
        "jobId": _JOB_ID,
        "status": status,
        "total": 1,
        "completed": 0 if asset_status == "pending" else 1,
        "assets": [{"name": "press-photo.jpg", "assetId": _ASSET_ID, "status": asset_status, "reason": None}],
        "gallerySize": None,
        "maxAssets": None,
        "receipts": [],
        "error": None,
        **extra,
    }


_QUEUED = (202, _enroll_job("queued", "pending"))
_RUNNING = (200, _enroll_job("running", "pending"))
_DONE = (200, _enroll_job("done", "enrolled", gallerySize=1, maxAssets=198, receipts=[{"assetId": _ASSET_ID}]))


def _scripted_handler(*replies: tuple[int, dict]):
    """Handler that answers each request with the next reply (repeating the last) and records it."""
    seen: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.content))
        status, body = replies[min(len(seen), len(replies)) - 1]
        return httpx.Response(status, json=body)

    return handler, seen


def test_sync_enroll_media_polls_the_job_until_done():
    handler, seen = _scripted_handler(_QUEUED, _RUNNING, _DONE)
    snapshots: list[str] = []
    with _sync_client(handler) as client:
        job = client.enroll_media(_ASSETS, poll_interval=0, on_progress=lambda j: snapshots.append(j.status))
    assert snapshots == ["queued", "running", "done"]
    assert [(method, path) for method, path, _ in seen] == [
        ("POST", "/api/v1/catalogue/assets"),
        ("GET", _JOB_PATH),
        ("GET", _JOB_PATH),
    ]
    assert json.loads(seen[0][2]) == {"assets": _ASSETS, "source": "api"}
    assert isinstance(job, CatalogueEnrollJob)
    assert job.assets[0].status == "enrolled"
    assert job.gallery_size == 1
    assert len(job.receipts) == 1


def test_enroll_media_returns_a_failed_job_rather_than_raising():
    failed = (200, _enroll_job("failed", "failed", error="not decodable"))
    handler, _ = _scripted_handler(_QUEUED, failed)
    with _sync_client(handler) as client:
        job = client.enroll_media(_ASSETS, poll_interval=0)
    assert job.status == "failed"
    assert job.error == "not decodable"


def test_enroll_media_gives_up_after_max_wait_and_names_the_job():
    handler, seen = _scripted_handler(_QUEUED, _RUNNING)
    with _sync_client(handler) as client, pytest.raises(CantiviaError, match=_JOB_ID):
        client.enroll_media(_ASSETS, max_wait=0)
    assert len(seen) == 1


def test_sync_start_enrollment_returns_the_202_job_without_waiting():
    handler, seen = _scripted_handler(_QUEUED, _DONE)
    with _sync_client(handler) as client:
        job = client.start_enrollment(_ASSETS, source="archive")
    assert job.status == "queued"
    assert job.job_id == _JOB_ID
    assert job.assets[0].asset_id == _ASSET_ID
    assert len(seen) == 1
    assert json.loads(seen[0][2])["source"] == "archive"


def test_sync_get_enrollment_job_reads_the_job_by_id():
    handler, seen = _scripted_handler(_DONE)
    with _sync_client(handler) as client:
        job = client.get_enrollment_job(_JOB_ID)
    assert seen == [("GET", _JOB_PATH, b"")]
    assert job.status == "done"


@pytest.mark.parametrize("bad", ["", "not-a-uuid", _JOB_ID.replace("-", ""), _JOB_ID + "/x", "../" + _JOB_ID])
def test_get_enrollment_job_rejects_a_malformed_id_before_sending(bad):
    handler, seen = _scripted_handler(_DONE)
    with _sync_client(handler) as client, pytest.raises(ValueError, match="invalid enrolment job id"):
        client.get_enrollment_job(bad)
    assert seen == []


def test_expired_enrollment_job_is_a_404():
    handler, _ = _scripted_handler((404, {"error": {"code": "not_found", "message": "expired"}}))
    with _sync_client(handler) as client, pytest.raises(NotFoundError) as exc:
        client.get_enrollment_job(_JOB_ID)
    assert exc.value.status_code == 404


def test_async_enroll_media_polls_the_job_until_done():
    handler, seen = _scripted_handler(_QUEUED, _RUNNING, _DONE)
    snapshots: list[str] = []

    async def run():
        client = _async_client(handler)
        try:
            return await client.enroll_media(_ASSETS, poll_interval=0, on_progress=lambda j: snapshots.append(j.status))
        finally:
            await client.aclose()

    job = asyncio.run(run())
    assert snapshots == ["queued", "running", "done"]
    assert len(seen) == 3
    assert job.assets[0].status == "enrolled"


def test_async_start_enrollment_and_get_enrollment_job():
    handler, seen = _scripted_handler(_QUEUED, _DONE)

    async def run():
        client = _async_client(handler)
        try:
            return await client.start_enrollment(_ASSETS), await client.get_enrollment_job(_JOB_ID)
        finally:
            await client.aclose()

    started, finished = asyncio.run(run())
    assert started.status == "queued"
    assert finished.status == "done"
    assert [(method, path) for method, path, _ in seen] == [("POST", "/api/v1/catalogue/assets"), ("GET", _JOB_PATH)]


def test_async_enroll_media_gives_up_after_max_wait():
    handler, _ = _scripted_handler(_QUEUED, _RUNNING)

    async def run():
        client = _async_client(handler)
        try:
            await client.enroll_media(_ASSETS, max_wait=0)
        finally:
            await client.aclose()

    with pytest.raises(CantiviaError, match=_JOB_ID):
        asyncio.run(run())
