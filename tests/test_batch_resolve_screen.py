"""Batches, follow-up resolution and the paper-mill screen."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from cantivia import (
    MAX_BATCH_CLAIMS,
    AsyncCantiviaClient,
    Batch,
    BatchAccepted,
    CantiviaClient,
    CantiviaError,
    NotFoundError,
    PaperMillScreenResult,
    ResolveResult,
    UnprocessableEntityError,
    Verdict,
)

BATCH_ID = "0b6f2c1e-9a4d-4e6f-8a7b-1c2d3e4f5a6b"
BATCH_PATH = f"/api/v1/batch/{BATCH_ID}"

ACCEPTED = {"batch_id": BATCH_ID, "total": 2, "status": "queued"}


def _batch(status: str, items: list[dict]) -> dict:
    done = sum(item["status"] == "done" for item in items)
    failed = sum(item["status"] == "failed" for item in items)
    return {
        "batch_id": BATCH_ID,
        "status": status,
        "total": len(items),
        "pending": len(items) - done - failed,
        "done": done,
        "failed": failed,
        "created_at": "2026-10-20T09:00:00Z",
        **({"finished_at": "2026-10-20T09:02:00Z"} if status in ("completed", "failed") else {}),
        "items": items,
    }


PENDING_ITEM = {"idx": 1, "claim": "The moon is cheese.", "status": "pending"}
DONE_ITEM = {
    "idx": 0,
    "claim": "Water boils at 100 C at sea level.",
    "status": "done",
    "verdict": "true",
    "confidence": 0.9,
    "reasoning": "Standard pressure.",
    "citations": [{"url": "https://example.org", "quote": "100 C"}],
    "degraded": False,
    "processing_ms": 2100,
}
FAILED_ITEM = {"idx": 1, "claim": "The moon is cheese.", "status": "failed", "error": "The check failed."}

RUNNING = _batch("running", [DONE_ITEM, PENDING_ITEM])
COMPLETED = _batch("completed", [DONE_ITEM, FAILED_ITEM])

RESOLUTION = {"claim": "Irish inflation fell in 2025.", "resolved": True, "method": "model"}

SCREENING = {
    "decision": "review",
    "score": 0.83,
    "band": 0.01,
    "precision": 0.62,
    "precisionInterval": [0.55, 0.69],
    "evidence": [["western blot", 0.41], ["lncrna", 0.33]],
    "caveats": ["Scores writing style, not misconduct."],
    "model": {"cohort": "cohort-a", "trainedThrough": 2024, "trainedOn": {"positives": 1000, "negatives": 9000}},
    "error": None,
}


def _scripted(*replies: tuple[int, dict]):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = replies[min(len(seen), len(replies)) - 1]
        return httpx.Response(status, json=body)

    return handler, seen


def _client(handler) -> CantiviaClient:
    inner = httpx.Client(transport=httpx.MockTransport(handler))
    return CantiviaClient(api_key="ctv_test", base_url="http://gw", max_retries=0, http_client=inner)


def _async_client(handler) -> AsyncCantiviaClient:
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncCantiviaClient(api_key="ctv_test", base_url="http://gw", max_retries=0, http_client=inner)


def test_submit_batch_posts_the_claims_and_returns_the_id():
    handler, seen = _scripted((202, ACCEPTED))
    with _client(handler) as client:
        accepted = client.submit_batch(["a", "b"])
    assert isinstance(accepted, BatchAccepted)
    assert accepted.batch_id == BATCH_ID
    assert accepted.status == "queued"
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/v1/batch"
    assert json.loads(seen[0].content) == {"claims": ["a", "b"], "kind": "claim"}
    assert "idempotency-key" not in seen[0].headers


def test_submit_batch_sends_the_kind_and_idempotency_key():
    handler, seen = _scripted((202, ACCEPTED))
    with _client(handler) as client:
        client.submit_batch(["article one", "article two"], kind="article", idempotency_key="batch-2026-10-20")
    assert json.loads(seen[0].content)["kind"] == "article"
    assert seen[0].headers["idempotency-key"] == "batch-2026-10-20"


def test_a_reused_idempotency_key_is_unprocessable():
    body = {"error": {"code": "idempotency_key_reused", "message": "Use a new key."}}
    handler, _ = _scripted((422, body))
    with _client(handler) as client, pytest.raises(UnprocessableEntityError) as exc:
        client.submit_batch(["a"], idempotency_key="k")
    assert exc.value.code == "idempotency_key_reused"


@pytest.mark.parametrize("claims", [[], [""], ["x" * 10_001], ["c"] * (MAX_BATCH_CLAIMS + 1), "not a list"])
def test_submit_batch_rejects_what_the_api_would_refuse_before_sending(claims):
    handler, seen = _scripted((202, ACCEPTED))
    with _client(handler) as client, pytest.raises(ValidationError):
        client.submit_batch(claims)
    assert seen == []


def test_get_batch_reads_progress_and_results():
    handler, seen = _scripted((200, COMPLETED))
    with _client(handler) as client:
        batch = client.get_batch(BATCH_ID)
    assert isinstance(batch, Batch)
    assert seen[0].url.path == BATCH_PATH
    assert batch.status == "completed"
    assert (batch.done, batch.failed, batch.pending) == (1, 1, 0)
    done, failed = batch.items
    assert done.verdict == Verdict.TRUE
    assert done.citations is not None
    assert done.citations[0].url == "https://example.org"
    assert done.processing_ms == 2100
    assert failed.error == "The check failed."
    assert failed.verdict is None


@pytest.mark.parametrize("bad", ["", "not-a-uuid", BATCH_ID + "/x", "../" + BATCH_ID])
def test_get_batch_rejects_a_malformed_id_before_sending(bad):
    handler, seen = _scripted((200, COMPLETED))
    with _client(handler) as client, pytest.raises(ValueError, match="invalid batch id"):
        client.get_batch(bad)
    assert seen == []


def test_another_keys_batch_is_not_found():
    handler, _ = _scripted((404, {"error": {"code": "not_found", "message": "No such batch."}}))
    with _client(handler) as client, pytest.raises(NotFoundError):
        client.get_batch(BATCH_ID)


def test_wait_for_batch_polls_until_it_finishes(sleeps):
    handler, seen = _scripted((200, RUNNING), (200, RUNNING), (200, COMPLETED))
    snapshots: list[str] = []
    with _client(handler) as client:
        batch = client.wait_for_batch(BATCH_ID, poll_interval=3, on_progress=lambda b: snapshots.append(b.status))
    assert snapshots == ["running", "running", "completed"]
    assert len(seen) == 3
    assert sleeps == [3, 3]
    assert batch.status == "completed"


def test_wait_for_batch_returns_a_failed_batch_rather_than_raising():
    handler, _ = _scripted((200, _batch("failed", [FAILED_ITEM])))
    with _client(handler) as client:
        assert client.wait_for_batch(BATCH_ID).status == "failed"


def test_wait_for_batch_gives_up_after_max_wait_and_names_the_batch():
    handler, seen = _scripted((200, RUNNING))
    with _client(handler) as client, pytest.raises(CantiviaError, match=BATCH_ID):
        client.wait_for_batch(BATCH_ID, max_wait=0)
    assert len(seen) == 1


def test_async_batch_round_trip(sleeps):
    handler, seen = _scripted((202, ACCEPTED), (200, RUNNING), (200, COMPLETED))

    async def run():
        async with _async_client(handler) as client:
            accepted = await client.submit_batch(["a", "b"])
            return await client.wait_for_batch(accepted.batch_id, poll_interval=1)

    batch = asyncio.run(run())
    assert batch.status == "completed"
    assert [request.method for request in seen] == ["POST", "GET", "GET"]
    assert sleeps == [1]


def test_async_wait_for_batch_gives_up_after_max_wait():
    handler, _ = _scripted((200, RUNNING))

    async def run():
        async with _async_client(handler) as client:
            await client.wait_for_batch(BATCH_ID, max_wait=0)

    with pytest.raises(CantiviaError, match=BATCH_ID):
        asyncio.run(run())


def test_resolve_sends_the_follow_up_and_prior_claims():
    handler, seen = _scripted((200, RESOLUTION))
    with _client(handler) as client:
        resolution = client.resolve("What about last year?", prior_claims=["Irish inflation fell in 2026."])
    assert isinstance(resolution, ResolveResult)
    assert resolution.resolved is True
    assert resolution.claim == "Irish inflation fell in 2025."
    assert seen[0].url.path == "/api/v1/resolve"
    assert json.loads(seen[0].content) == {
        "priorClaims": ["Irish inflation fell in 2026."],
        "followUp": "What about last year?",
    }


def test_resolve_without_prior_claims_sends_an_empty_list():
    handler, seen = _scripted((200, {"claim": "Hello", "resolved": False, "method": "passthrough"}))
    with _client(handler) as client:
        resolution = client.resolve("Hello")
    assert resolution.resolved is False
    assert json.loads(seen[0].content)["priorClaims"] == []


@pytest.mark.parametrize(("follow_up", "prior"), [("", None), ("x" * 4001, None), ("ok", ["c"] * 21)])
def test_resolve_rejects_what_the_api_would_refuse(follow_up, prior):
    handler, seen = _scripted((200, RESOLUTION))
    with _client(handler) as client, pytest.raises(ValidationError):
        client.resolve(follow_up, prior_claims=prior)
    assert seen == []


def test_async_resolve():
    handler, _ = _scripted((200, RESOLUTION))

    async def run():
        async with _async_client(handler) as client:
            return await client.resolve("And last year?", prior_claims=["c"])

    assert asyncio.run(run()).method == "model"


def test_screen_manuscript_sends_title_abstract_and_budget():
    handler, seen = _scripted((200, SCREENING))
    with _client(handler) as client:
        screening = client.screen_manuscript("A title", "An abstract.", budget=0.01)
    assert isinstance(screening, PaperMillScreenResult)
    assert seen[0].url.path == "/api/v1/paper-mill/screen"
    assert json.loads(seen[0].content) == {"title": "A title", "abstract": "An abstract.", "budget": 0.01}
    assert screening.decision == "review"
    assert screening.precision_interval == (0.55, 0.69)
    assert screening.evidence[0] == ("western blot", 0.41)
    assert screening.model is not None
    assert screening.model.trained_through == 2024
    assert screening.caveats


def test_screen_manuscript_omits_an_unset_budget():
    unavailable = {
        **SCREENING,
        "decision": "unavailable",
        "score": None,
        "band": None,
        "precision": None,
        "precisionInterval": None,
        "evidence": [],
        "model": None,
        "error": "The screen is unavailable.",
    }
    handler, seen = _scripted((200, unavailable))
    with _client(handler) as client:
        screening = client.screen_manuscript(abstract="Only an abstract.")
    assert json.loads(seen[0].content) == {"title": "", "abstract": "Only an abstract."}
    assert screening.decision == "unavailable"
    assert screening.score is None
    assert screening.model is None


@pytest.mark.parametrize("budget", [0, -0.1, 1.5])
def test_screen_manuscript_rejects_a_budget_outside_zero_to_one(budget):
    handler, seen = _scripted((200, SCREENING))
    with _client(handler) as client, pytest.raises(ValidationError):
        client.screen_manuscript("t", "a", budget=budget)
    assert seen == []


@pytest.mark.parametrize(("title", "abstract"), [("", ""), ("  ", "\n")])
def test_screen_manuscript_rejects_a_manuscript_with_no_text(title, abstract):
    handler, seen = _scripted((200, SCREENING))
    with _client(handler) as client, pytest.raises(ValueError, match="a title or an abstract is required"):
        client.screen_manuscript(title, abstract)
    assert seen == []


def test_async_screen_manuscript():
    handler, _ = _scripted((200, SCREENING))

    async def run():
        async with _async_client(handler) as client:
            return await client.screen_manuscript("t", "a")

    assert asyncio.run(run()).decision == "review"
