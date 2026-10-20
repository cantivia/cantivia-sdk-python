"""Offline tests for Titrate in the Python client: parsing, payloads, score_post."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from cantivia import (
    AsyncCantiviaClient,
    CantiviaClient,
    PostTitrateRequest,
    Titrate,
    TitrateAbstainReason,
    TitrateChunk,
    VerificationError,
    VerificationRequest,
    parse_chunk,
)

POST_URL = "https://bsky.app/profile/council.example/post/3root"

SCORED = {
    "scored": True,
    "abstainReason": None,
    "detail": None,
    "platform": "bluesky",
    "postUri": "at://did:plc:rootauthor/app.bsky.feed.post/3root",
    "probability": 0.7518,
    "isViral": True,
    "decisionThreshold": 0.46925350832174473,
    "viralThresholdSize": 17,
    "horizonHours": 1,
    "observedNodes": 16,
    "nodesInWindow": 14,
    "elapsedHours": 2,
    "extrapolated": False,
    "sizeRange": {"low": 14, "high": 25, "level": 0.8},
    "model": "titrate",
    "artifactSha256": "ab" * 32,
    "artifactFormatVersion": 6,
}

ABSTAINED = {
    **SCORED,
    "scored": False,
    "abstainReason": "window_open",
    "detail": "the 1 h window has not closed yet (0.5 h elapsed)",
    "probability": None,
    "isViral": None,
    "nodesInWindow": None,
    "sizeRange": None,
}

DONE = {
    "type": "done",
    "result": {
        "claim": "c",
        "verdict": "fake",
        "confidence": 0.9,
        "reasoning": "r",
        "citations": [],
        "cacheHit": False,
        "route": "route-a",
        "processingMs": 10,
    },
}


def _sse(*chunks: dict) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)


def _handler(body: str, seen: dict):
    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return handler


def _client(body: str, seen: dict) -> CantiviaClient:
    inner = httpx.Client(transport=httpx.MockTransport(_handler(body, seen)))
    return CantiviaClient(api_key="ctv_test", base_url="http://test", http_client=inner)


def test_parse_a_scored_titrate_chunk():
    chunk = parse_chunk(json.dumps({"type": "titrate", "result": SCORED}))
    assert isinstance(chunk, TitrateChunk)
    assert chunk.result.is_viral is True
    assert chunk.result.viral_threshold_size == 17
    assert chunk.result.observed_nodes == 16
    assert chunk.result.size_range is not None
    assert (chunk.result.size_range.low, chunk.result.size_range.high, chunk.result.size_range.level) == (14, 25, 0.8)


def test_an_answer_without_a_size_range_still_parses():
    sparse = {k: v for k, v in SCORED.items() if k != "sizeRange"}
    chunk = parse_chunk(json.dumps({"type": "titrate", "result": sparse}))
    assert isinstance(chunk, TitrateChunk)
    assert chunk.result.size_range is None


def test_parse_an_abstention():
    chunk = parse_chunk(json.dumps({"type": "titrate", "result": ABSTAINED}))
    assert isinstance(chunk, TitrateChunk)
    assert chunk.result.scored is False
    assert chunk.result.abstain_reason is TitrateAbstainReason.WINDOW_OPEN
    assert chunk.result.probability is None


def test_an_unknown_abstain_reason_keeps_its_raw_value():
    # A reason a newer server adds must not fail the parse for this release.
    chunk = parse_chunk(
        json.dumps({"type": "titrate", "result": {**ABSTAINED, "abstainReason": "bored", "platform": "mastodon"}})
    )
    assert isinstance(chunk, TitrateChunk)
    assert chunk.result.abstain_reason == "bored"
    assert chunk.result.platform == "mastodon"


def test_a_claim_request_sends_its_post_under_the_wire_name():
    payload = VerificationRequest(claim="c", post_url=POST_URL).model_dump(by_alias=True, exclude_none=True)
    assert payload == {"claim": "c", "language": "en", "priority": "standard", "postUrl": POST_URL}


def test_a_claim_request_without_a_post_is_unchanged():
    payload = VerificationRequest(claim="c").model_dump(by_alias=True, exclude_none=True)
    assert payload == {"claim": "c", "language": "en", "priority": "standard"}


def test_a_post_request_payload():
    assert PostTitrateRequest(post_url=POST_URL).model_dump(by_alias=True) == {"postUrl": POST_URL, "kind": "post"}


def test_score_post_returns_the_answer_and_sends_kind_post():
    seen: dict = {}
    with _client(_sse({"type": "titrate", "result": SCORED}), seen) as client:
        result = client.score_post(POST_URL)
    assert isinstance(result, Titrate)
    assert result.is_viral is True
    assert seen["body"] == {"postUrl": POST_URL, "kind": "post"}


def test_score_post_raises_when_the_server_has_it_switched_off():
    seen: dict = {}
    with (
        _client(_sse({"type": "error", "message": "Titrate post scoring is not enabled."}), seen) as client,
        pytest.raises(VerificationError, match="not enabled"),
    ):
        client.score_post(POST_URL)


def test_async_score_post():
    seen: dict = {}
    transport = httpx.MockTransport(_handler(_sse({"type": "titrate", "result": ABSTAINED}), seen))

    async def run() -> Titrate:
        inner = httpx.AsyncClient(transport=transport)
        async with AsyncCantiviaClient(api_key="ctv_test", base_url="http://test", http_client=inner) as c:
            return await c.score_post(POST_URL)

    result = asyncio.run(run())
    assert result.abstain_reason is TitrateAbstainReason.WINDOW_OPEN


def test_verify_forwards_the_post_and_still_returns_the_verdict():
    seen: dict = {}
    with _client(_sse({"type": "titrate", "result": SCORED}, DONE), seen) as client:
        result = client.verify("c", post_url=POST_URL)
    assert result.claim == "c"
    assert seen["body"]["postUrl"] == POST_URL
