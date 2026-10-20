"""Offline tests: SSE parsing and contract model parsing (no network)."""

import pytest

from cantivia import (
    VERDICT_LABELS,
    ArticleDoneChunk,
    Batch,
    CacheResultChunk,
    CantiviaError,
    CatalogueEnrollJob,
    ClaimsExtractedChunk,
    DebateArgumentChunk,
    DebateDecomposedChunk,
    DebateRoundChunk,
    DoneChunk,
    ErrorChunk,
    FactcheckCompleteChunk,
    FactcheckThinkingChunk,
    MediaDoneChunk,
    MediaForensicsChunk,
    PaperMillScreenResult,
    RoutingChunk,
    SearchCompleteChunk,
    Verdict,
    VerificationResult,
    parse_chunk,
)
from cantivia._sse import iter_sse

SAMPLE_STREAM = (
    'data: {"type":"routing","action":"route-a","difficulty":0.7}\n\n'
    'data: {"type":"cache_result","hit":false}\n\n'
    'data: {"type":"search_complete","resultCount":5}\n\n'
    'data: {"type":"factcheck_complete","verdict":"fake","confidence":0.91}\n\n'
    'data: {"type":"done","result":{"claim":"x","verdict":"fake","confidence":0.91,'
    '"reasoning":"because","citations":[{"url":"https://e.com","quote":"q"}],'
    '"cacheHit":false,"route":"route-a","processingMs":1234}}\n\n'
)


def test_sse_splits_events():
    events = list(iter_sse(SAMPLE_STREAM.splitlines()))
    assert len(events) == 5


def test_parse_each_chunk_type():
    events = list(iter_sse(SAMPLE_STREAM.splitlines()))
    chunks = [parse_chunk(e) for e in events]

    assert isinstance(chunks[0], RoutingChunk)
    assert chunks[0].action == "route-a"

    assert isinstance(chunks[3], FactcheckCompleteChunk)
    assert chunks[3].verdict is Verdict.FAKE

    done = chunks[-1]
    assert isinstance(done, DoneChunk)
    result = done.result
    assert isinstance(result, VerificationResult)
    # camelCase JSON maps onto snake_case attributes
    assert result.cache_hit is False
    assert result.processing_ms == 1234
    assert result.citations[0].url == "https://e.com"


def test_search_complete_degraded_flag():
    # Present and true when the search found no usable evidence.
    degraded = parse_chunk('{"type":"search_complete","resultCount":0,"degraded":true}')
    assert isinstance(degraded, SearchCompleteChunk)
    assert degraded.degraded is True
    # Absent on a normal result — defaults to False.
    normal = parse_chunk('{"type":"search_complete","resultCount":5}')
    assert isinstance(normal, SearchCompleteChunk)
    assert normal.degraded is False


def test_cache_result_source_field():
    hit = parse_chunk('{"type":"cache_result","hit":true,"score":0.9,"source":"corpus"}')
    assert isinstance(hit, CacheResultChunk)
    assert hit.source == "corpus"
    # Absent on a miss: defaults to None.
    miss = parse_chunk('{"type":"cache_result","hit":false}')
    assert isinstance(miss, CacheResultChunk)
    assert miss.source is None


def test_cache_result_source_this_release_does_not_know_keeps_its_raw_value():
    # A diagnostic field must never fail the stream it rides in.
    chunk = parse_chunk('{"type":"cache_result","hit":true,"score":0.9,"source":"some-future-store"}')
    assert isinstance(chunk, CacheResultChunk)
    assert chunk.source == "some-future-store"


# A published release cannot be changed, so a value a newer server adds to any
# response enum must reach the caller as its raw string rather than fail the parse.


def test_an_unknown_verdict_keeps_its_raw_value():
    done = parse_chunk(
        '{"type":"done","result":{"claim":"x","verdict":"satire","confidence":0.5,"reasoning":"r",'
        '"cacheHit":false,"route":"route-a","processingMs":1}}'
    )
    assert isinstance(done, DoneChunk)
    assert done.result.verdict == "satire"
    assert VERDICT_LABELS.get(done.result.verdict, done.result.verdict) == "satire"
    complete = parse_chunk('{"type":"factcheck_complete","verdict":"satire","confidence":0.5}')
    assert isinstance(complete, FactcheckCompleteChunk)
    assert complete.verdict == "satire"
    # A known value still parses to the enum member.
    known = parse_chunk('{"type":"reasoning_complete","verdict":"mostly_fake"}')
    assert known.verdict is Verdict.MOSTLY_FAKE


def test_an_unknown_status_keeps_its_raw_value():
    batch = Batch.model_validate(
        {
            "batch_id": "b",
            "status": "paused",
            "total": 1,
            "pending": 1,
            "done": 0,
            "failed": 0,
            "created_at": "2026-10-20T00:00:00Z",
            "items": [{"idx": 0, "claim": "c", "status": "retrying"}],
        }
    )
    assert batch.status == "paused"
    assert batch.items[0].status == "retrying"
    job = CatalogueEnrollJob.model_validate(
        {"jobId": "j", "status": "throttled", "total": 0, "completed": 0, "assets": []}
    )
    assert job.status == "throttled"
    screening = PaperMillScreenResult.model_validate({"decision": "escalate"})
    assert screening.decision == "escalate"


def test_an_unknown_value_in_a_nested_object_keeps_its_raw_value():
    forensics = parse_chunk(
        '{"type":"media_forensics","modality":"hologram","result":{"verdict":"edited","method":"fisher",'
        '"combinedP":null,"channels":[{"channel":"c2pa","verdict":"edited","pValue":null,'
        '"coverageValid":true,"scope":"s"}],"excised":[],"alpha":0.01}}'
    )
    assert isinstance(forensics, MediaForensicsChunk)
    assert forensics.modality == "hologram"
    assert forensics.result.method == "fisher"
    assert forensics.result.channels[0].verdict == "edited"

    article_done = parse_chunk(
        '{"type":"article_done","result":{"claimCount":1,"verifiedCount":1,"degradedCount":0,'
        '"overallVerdict":"satire","overallConfidence":0.5,"summary":"s","extractionMethod":"model",'
        '"claims":[{"claim":"x","verdict":"satire","confidence":0.5,"reasoning":"r",'
        '"cacheHit":false,"route":"route-a","processingMs":1}],"processingMs":10}}'
    )
    assert isinstance(article_done, ArticleDoneChunk)
    assert article_done.result.overall_verdict == "satire"
    assert article_done.result.claims[0].verdict == "satire"

    extracted = parse_chunk(
        '{"type":"claims_extracted","count":1,"method":"model","claims":[{"claim":"c",'
        '"checkWorthiness":0.8,"claimType":"comparative","originalText":"o","verifiable":true}]}'
    )
    assert isinstance(extracted, ClaimsExtractedChunk)
    assert extracted.claims[0].claim_type == "comparative"


def test_search_complete_evidence_and_sources():
    chunk = parse_chunk(
        '{"type":"search_complete","resultCount":2,"evidenceChars":1500,'
        '"sources":[{"title":"Wikipedia","url":"https://en.wikipedia.org"}]}'
    )
    assert isinstance(chunk, SearchCompleteChunk)
    # camelCase JSON maps onto the snake_case attribute.
    assert chunk.evidence_chars == 1500
    assert chunk.sources is not None
    assert chunk.sources[0].title == "Wikipedia"
    assert chunk.sources[0].url == "https://en.wikipedia.org"
    # Both optional — default to None.
    bare = parse_chunk('{"type":"search_complete","resultCount":0}')
    assert isinstance(bare, SearchCompleteChunk)
    assert bare.evidence_chars is None
    assert bare.sources is None


def test_verification_result_sources_field():
    result = VerificationResult.model_validate(
        {
            "claim": "c",
            "verdict": "true",
            "confidence": 0.9,
            "reasoning": "r",
            "citations": [],
            "sources": ["https://a.com", "https://b.com"],
            "cacheHit": False,
            "route": "route-a",
            "processingMs": 100,
        }
    )
    assert result.sources == ["https://a.com", "https://b.com"]
    # Optional — defaults to None.
    no_sources = VerificationResult.model_validate(
        {
            "claim": "c",
            "verdict": "true",
            "confidence": 0.9,
            "reasoning": "r",
            "citations": [],
            "cacheHit": False,
            "route": "route-a",
            "processingMs": 100,
        }
    )
    assert no_sources.sources is None


def test_verification_result_degraded_flag_and_fields_it_does_not_model():
    base = {
        "claim": "c",
        "verdict": "unverifiable",
        "confidence": 0.0,
        "reasoning": "r",
        "citations": [],
        "cacheHit": False,
        "route": "route-a",
        "processingMs": 100,
    }
    # Absent means a verdict is a judgment, not a fallback.
    assert VerificationResult.model_validate(base).degraded is False
    # Fields this release does not model are ignored, not rejected.
    result = VerificationResult.model_validate({**base, "degraded": True, "timings": {"total": 100.0}})
    assert result.degraded is True
    assert "timings" not in result.model_dump()


def test_sse_joins_multiline_data_block():
    # A single event split across two `data:` lines is joined with a newline.
    stream = 'data: {"type":"error",\ndata: "message":"split across lines"}\n\n'
    events = list(iter_sse(stream.splitlines()))
    assert len(events) == 1
    assert events[0] == '{"type":"error",\n"message":"split across lines"}'
    chunk = parse_chunk(events[0])
    assert isinstance(chunk, ErrorChunk)
    assert chunk.message == "split across lines"


def test_sse_drops_unterminated_trailing_event():
    # The second event has no terminating blank line (mid-stream disconnect);
    # it must be dropped rather than emitted as a truncated payload.
    stream = (
        'data: {"type":"routing","action":"a","difficulty":0.1}\n'
        "\n"
        'data: {"type":"done","result":{'  # truncated, no blank line after
    )
    events = list(iter_sse(stream.splitlines()))
    assert len(events) == 1
    assert parse_chunk(events[0]).type == "routing"


def test_parse_debate_chunks():
    dec = parse_chunk('{"type":"debate_decomposed","subClaims":["a","b"]}')
    assert isinstance(dec, DebateDecomposedChunk)
    assert dec.sub_claims == ["a", "b"]

    rnd = parse_chunk('{"type":"debate_round","round":0,"subClaimIndex":1}')
    assert isinstance(rnd, DebateRoundChunk)
    assert rnd.round == 0
    assert rnd.sub_claim_index == 1

    arg = parse_chunk(
        '{"type":"debate_argument","role":"role-a","content":"...","confidence":0.4,"round":0,"subClaimIndex":0}'
    )
    assert isinstance(arg, DebateArgumentChunk)
    assert arg.role == "role-a"
    # camelCase JSON maps onto the snake_case attribute.
    assert arg.sub_claim_index == 0

    # A field this release does not model is ignored.
    sparse = parse_chunk(
        '{"type":"debate_argument","role":"role-b","content":"...",'
        '"confidence":0.4,"round":1,"strategyLabel":"x","subClaimIndex":0}'
    )
    assert isinstance(sparse, DebateArgumentChunk)
    assert sparse.round == 1


def test_parse_article_chunks():
    extracted = parse_chunk(
        '{"type":"claims_extracted","count":1,"method":"model","claims":['
        '{"claim":"c","checkWorthiness":0.8,"claimType":"numerical",'
        '"originalText":"o","entities":["E"],"verifiable":true}]}'
    )
    assert isinstance(extracted, ClaimsExtractedChunk)
    assert extracted.claims[0].claim_type == "numerical"
    assert extracted.claims[0].check_worthiness == 0.8

    article_done = parse_chunk(
        '{"type":"article_done","result":{"claimCount":1,"verifiedCount":1,"degradedCount":0,"overallVerdict":"true",'
        '"overallConfidence":0.9,"summary":"s","extractionMethod":"model",'
        '"claims":[],"processingMs":10}}'
    )
    assert isinstance(article_done, ArticleDoneChunk)
    assert article_done.result.overall_verdict is Verdict.TRUE
    assert article_done.result.claim_count == 1


def test_terminal_chunks_carrying_billable_usage_still_parse():
    # Terminal chunks may carry a `usage` object beside `result`. This release of the
    # client does not model it, and must not fail on it.
    usage = '"usage":{"inputTokens":7000,"outputTokens":3000,"searches":1},'
    done = parse_chunk(
        '{"type":"done",' + usage + '"result":{"claim":"x","verdict":"true","confidence":0.9,'
        '"reasoning":"r","citations":[],"cacheHit":false,"route":"route-a","processingMs":1}}'
    )
    assert isinstance(done, DoneChunk)
    assert done.result.verdict is Verdict.TRUE

    article_done = parse_chunk(
        '{"type":"article_done",' + usage + '"result":{"claimCount":1,"verifiedCount":1,'
        '"degradedCount":0,"overallVerdict":"true","overallConfidence":0.9,"summary":"s",'
        '"extractionMethod":"model","claims":[],"processingMs":10}}'
    )
    assert isinstance(article_done, ArticleDoneChunk)
    assert article_done.result.claim_count == 1


def test_parse_media_forensics_and_thinking_chunks():
    thinking = parse_chunk('{"type":"factcheck_thinking","delta":"weighing"}')
    assert isinstance(thinking, FactcheckThinkingChunk)
    assert thinking.delta == "weighing"

    current = parse_chunk(
        '{"type":"media_forensics","modality":"image","result":{"verdict":"synthetic","method":"hard",'
        '"combinedP":0,"channels":[{"channel":"retrieval","verdict":"synthetic","pValue":null,'
        '"coverageValid":true,"scope":"enrolled set","caveats":["A no-match is unknown, not authentic."],'
        '"detail":"matches enrolled asset(s): poster.png","gradeCeiling":"G-enrolled",'
        '"scopeCaveats":["single-operation edits"],"gallerySize":12,'
        '"evidence":{"package_seal":"abc"}}],"excised":[],"alpha":0.01,"unavailable":[]}}'
    )
    assert isinstance(current, MediaForensicsChunk)
    channel = current.result.channels[0]
    # camelCase JSON maps onto snake_case attributes.
    assert channel.grade_ceiling == "G-enrolled"
    assert channel.scope_caveats == ["single-operation edits"]
    assert channel.gallery_size == 12
    assert channel.evidence == {"package_seal": "abc"}

    # A result without the optional disclosure fields, and with fields this release does
    # not model: the first default and the second are ignored, never rejected.
    sparse = parse_chunk(
        '{"type":"media_forensics","modality":"image","result":{"verdict":"unknown","method":"none",'
        '"combinedP":null,"channels":[{"channel":"c2pa","verdict":"unknown","pValue":null,'
        '"coverageValid":true,"scope":"s","caveats":["c"],"model":"model-a","controlNumbers":["x"]}],'
        '"excised":[],"alpha":0.01}}'
    )
    assert isinstance(sparse, MediaForensicsChunk)
    assert sparse.result.channels[0].detail == ""
    assert sparse.result.unavailable == []


def test_parse_media_done_chunk():
    done = parse_chunk('{"type":"media_done","modality":"audio"}')
    assert isinstance(done, MediaDoneChunk)
    assert done.modality == "audio"


def test_parse_chunk_rejects_a_real_media_verdict():
    # There is no "real" member; a server that sent one is broken, not informative.
    with pytest.raises(CantiviaError):
        parse_chunk(
            '{"type":"media_forensics","modality":"image","result":{"verdict":"real","method":"none",'
            '"combinedP":null,"channels":[],"excised":[],"alpha":0.01}}'
        )


def test_parse_chunk_returns_none_for_a_type_it_does_not_know():
    # A newer API may add event types; this release skips them rather than failing.
    assert parse_chunk('{"type":"heartbeat"}') is None
    assert parse_chunk('{"type":"event_from_a_newer_api","result":{"anything":1}}') is None


def test_parse_chunk_still_rejects_a_known_type_with_bad_fields():
    with pytest.raises(CantiviaError, match="unrecognised chunk"):
        parse_chunk('{"type":"routing","action":"a"}')


def test_parse_chunk_rejects_a_payload_without_a_type():
    with pytest.raises(CantiviaError):
        parse_chunk('{"message":"no type"}')


def test_parse_chunk_wraps_malformed_json():
    with pytest.raises(CantiviaError):
        parse_chunk('{"type":"routing", not valid json')


def test_verification_result_alias_roundtrip():
    result = VerificationResult.model_validate(
        {
            "claim": "c",
            "verdict": "partially_true",
            "confidence": 0.5,
            "reasoning": "r",
            "citations": [],
            "cacheHit": True,
            "route": "cache",
            "processingMs": 12.5,
        }
    )
    assert result.verdict is Verdict.PARTIALLY_TRUE
    assert result.cache_hit is True
