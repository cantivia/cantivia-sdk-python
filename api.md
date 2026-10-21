# API reference

Every method below exists on `CantiviaClient` and, as a coroutine, on
`AsyncCantiviaClient`. The `*_stream` methods return an iterator (an async iterator on
the async client) of `StreamChunk`.

Every request method also accepts two keyword arguments that override the client's
settings for that call:

- `timeout: float | httpx.Timeout | None` - seconds without data before the request
  times out (default 600 s without data, 10 s to connect).
- `max_retries: int | None` - retries for requests that never reached the service.

Types are importable from `cantivia`.

Where a response field lists its values below, it is one of these today; treat any
other value as unknown. A newer server can add a value, and this release passes it
through as its raw string, so such a field is typed `Literal[...] | str` or
`Enum | str`. A known value still parses to its enum member.

## Client

```python
CantiviaClient(
    api_key: str | None = None,          # default: CANTIVIA_API_KEY
    *,
    base_url: str | None = None,         # default: CANTIVIA_BASE_URL, then https://api.cantivia.com
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,   # 600 s, 10 s to connect
    max_retries: int = DEFAULT_MAX_RETRIES,             # 2
    http_client: httpx.Client | None = None,
)
AsyncCantiviaClient(...)                 # same arguments; http_client is an httpx.AsyncClient
```

| method | returns | what it does |
|---|---|---|
| `close()` / `await aclose()` | `None` | release the client's connections |
| `health()` | `dict[str, Any]` | check that the API is up; needs no API key |

`api_key` is required for every method but `health()`; create one on the Cantivia
dashboard. Use the client as a context manager (`with`, or `async with` for
`AsyncCantiviaClient`), or call `close()` when you are done, so its connections are
released.

Pass your own `httpx.Client` (an `httpx.AsyncClient` for the async client) as
`http_client` to set a proxy or a custom transport; the options above still apply to
every request. Each request sends `User-Agent: cantivia-python/<version>`.

## Retries and timeouts

The client retries only requests that provably never reached the service: a connection
that could not be opened (refused, reset while connecting, DNS failure, connect
timeout), `429 Too Many Requests` and `503 Service Unavailable`. Both statuses are
refusals made before any work starts. Once a request has been sent it is never retried:
not after a timeout, a dropped connection, any other 5xx or 4xx, or a stream that has
started, because the work may already be under way and would run, and be billed, twice.

It waits as long as the API's `Retry-After` header asks, up to 60 seconds, and otherwise
backs off exponentially from 0.5 s to at most 8 s with jitter. The default is 2 retries;
set `max_retries` on the client or on one call, and 0 turns retrying off.

A request times out when it goes 600 s without data, by default, with 10 seconds to
connect. A stream counts every event as data, so a long check that keeps streaming
progress does not time out. Pass `timeout` as a number of seconds or an `httpx.Timeout`,
on the client or on one call. A timeout raises `APITimeoutError` and is not retried.

```python
client = CantiviaClient(max_retries=0, timeout=120.0)
client.screen_manuscript(title, abstract, max_retries=5, timeout=httpx.Timeout(900.0, connect=5.0))
```

## Media forensics

`verify_media` checks an image, audio clip or video for C2PA provenance and matches it
against media your account has enrolled. The verdict is `synthetic` or `unknown`, never
"real": `unknown` means nothing was found, not that the file is authentic.

| method | returns |
|---|---|
| `verify_media(media, modality, *, mime=None)` | `MediaForensicsResult` |
| `verify_media_stream(media, modality, *, mime=None)` | iterator of `StreamChunk`, ending in `MediaDoneChunk` |

- `media: str` - base64 file. `modality: Literal["image", "audio", "video"]`.
  `mime: str | None` - the file's media type, for audio and video.

`MediaForensicsResult`: `verdict: Literal["synthetic", "unknown"] | str`, `method`,
`channels: list[MediaForensicsChannel]`, `excised`, `alpha`,
`unavailable: list[MediaForensicsUnavailable]` (channels that could not run, and why),
and `combined_p` (programmatic use only).

`MediaForensicsChannel`: `channel`, `verdict`, `coverage_valid`, `scope`, `caveats`,
`detail`, `grade_ceiling`, `scope_caveats`, `gallery_size`, `evidence`, and `p_value`
(programmatic use only). Show `detail`, `caveats` and `scope_caveats` with the verdict.

`verify_image_stream(image, forensics_only=True)` runs forensics alone on an image: one
`MediaForensicsChunk`, then `MediaDoneChunk`, with no text reading and no fact-check.

## Media catalogue

Enrol media you hold the rights to, and later checks by any key on your account match
copies of it. Only your account sees it. `draft_notice` drafts a takedown notice for a
copy found online, for a person to review before anything is sent;
`delete_catalogue_asset` erases an asset.

| method | returns |
|---|---|
| `enroll_media(assets, *, source="api", max_wait=600, poll_interval=2, on_progress=None)` | `CatalogueEnrollJob` |
| `start_enrollment(assets, *, source="api")` | `CatalogueEnrollJob` |
| `get_enrollment_job(job_id)` | `CatalogueEnrollJob` |
| `list_catalogue()` | `CatalogueListResponse` |
| `delete_catalogue_asset(asset_id)` | `CatalogueDeleteResponse` |
| `draft_notice(name, media, *, infringing_url, complainant, host="")` | `CatalogueNoticeResponse` |

- `assets: list[CatalogueAsset] | list[dict[str, str]]` - up to 10 (`MAX_CATALOGUE_BATCH`)
  of `{"name": ..., "media": <base64>}`.
- `on_progress: Callable[[CatalogueEnrollJob], None] | None` - called with every snapshot.
- `job_id: str` - the UUID `start_enrollment` answered with. `asset_id: str` - the
  64-character lowercase hex SHA-256 of the asset's bytes. Malformed ids raise
  `ValueError` before any request.
- `complainant: Complainant | dict[str, str]` - `name`, and optionally `organisation`,
  `email`, `address`.

`enroll_media` raises `CantiviaError` naming the job if it is unfinished after
`max_wait` seconds; the job keeps running.

`CatalogueEnrollJob`: `job_id`, `status: Literal["queued", "running", "done", "failed"] | str`,
`total`, `completed`, `assets: list[CatalogueJobAsset]` (`name`, `asset_id`, `status`,
`reason`), `gallery_size`, `max_assets`, `receipts`, `error`.

`CatalogueListResponse`: `assets: list[CatalogueAssetRecord]` (`asset_id`, `name`,
`modality`, `enrolled_at`, `source`, `bytes_len`), `gallery_size`, `max_assets`.

`CatalogueDeleteResponse`: `deleted`, `asset_id`, `gallery_size`, `max_assets`.

`CatalogueNoticeResponse`: `outcome: Literal["MATCH", "UNKNOWN"] | str`,
`status: Literal["READY_FOR_REVIEW"] | str | None`, `notice`, `notice_text`, `detail`.

## Titrate

`score_post` gives an early warning on whether a social post's thread will grow large.
It scores spread, not truth, and often declines to score: most posts get no replies, and
a post under an hour old has not finished its observation window. Read
`abstain_reason`.

| method | returns |
|---|---|
| `score_post(post_url)` | `Titrate` |
| `score_post_stream(post_url)` | iterator of `StreamChunk` with one `TitrateChunk` |

- `post_url: str` - a bsky.app post link or an `at://` post URI.

`Titrate`: `scored`, `abstain_reason: TitrateAbstainReason | str | None`, `detail`, `platform`,
`post_uri`, `is_viral`, `decision_threshold`, `viral_threshold_size`, `horizon_hours`,
`observed_nodes`, `nodes_in_window`, `elapsed_hours`, `extrapolated`,
`size_range: SizeRange | None` (`low`, `high`, `level`), `model`, `artifact_sha256` and
`artifact_format_version` (which version of the scorer answered), and `probability`
(programmatic use only). `platform` is `"bluesky"` for every scored post today;
`"reddit"` is reserved. `detail` explains `abstain_reason` in words.

## Paper-mill screen

`screen_manuscript` reads a manuscript's title and abstract and says whether an
integrity reviewer should look at it. It scores writing style, not misconduct, so show
the `caveats` with the decision. `unavailable` means the screen could not run, and
`error` says why.

| method | returns |
|---|---|
| `screen_manuscript(title="", abstract="", *, budget=None)` | `PaperMillScreenResult` |

- `title: str` - up to 2,000 characters. `abstract: str` - up to 20,000 characters. At
  least one must hold text; an empty manuscript raises `ValueError` before any request.
- `budget: float | None` - the flag rate your reviewers can staff, greater than 0 and at
  most 1.

`PaperMillScreenResult`: `decision: Literal["review", "pass", "unavailable"] | str`, `score`,
`band` (the flag rate cleared), `precision` and `precision_interval` (measured at that
band on held-out papers), `evidence: list[tuple[str, float]]`, `caveats`,
`model: ScreeningModel | None` (which version of the screen answered: `cohort`, the
collection it was trained on; `trained_through`, the last publication year in it;
`trained_on`, counts describing it), `error`.

## Usage

| method | returns |
|---|---|
| `usage()` | `Usage` |

`Usage`: `key_id`, `requests: UsageRequests` (`window`, `today`, `month`),
`rate_limit: UsageRateLimit` (`limit`, `remaining`, `window_seconds`),
`verdicts: dict[str, int]`, `latency_ms: UsageLatency` (`p50`, `p95`, `samples`),
`products: list[UsageProduct]`.

`UsageProduct`: `product`, `status` (`"pilot"`, `"active"`, `"past_due"`, `"cancelled"`
or `"none"`), `tier`, `pilot_ends_at`, `units: list[UsageUnit]` (`unit`, `used`,
`allowance`, `overage`).

The request, verdict and latency counters are approximate and scoped to the calling key;
`products` is exact billing state for the whole account.

## Stream chunks

Long-running checks have a `*_stream` form that yields typed chunks as the work
progresses. Streaming is the recommended way to run long work: progress arrives as it
happens, rather than after one long wait. With `AsyncCantiviaClient`, iterate with
`async for`. Dispatch on the chunk's class, for example
`isinstance(chunk, MediaForensicsChunk)`; a failure arrives as an `ErrorChunk`.

`StreamChunk` is a union discriminated by `type`. Media forensics and Titrate streams
use these; fact-checking streams add the chunks listed under
[Fact-checking (coming soon)](#fact-checking-coming-soon).

| `type` | class | carries |
|---|---|---|
| `media_forensics` | `MediaForensicsChunk` | `modality`, `result: MediaForensicsResult` |
| `media_done` | `MediaDoneChunk` | `modality` |
| `titrate` | `TitrateChunk` | `result: Titrate` |
| `error` | `ErrorChunk` | `message`, `request_id` (the stream's request id, set by the client) |

`parse_chunk(data: str) -> StreamChunk | None` parses one server-sent event payload. It
returns None for an event type this version does not know, and the stream methods skip
those, so a newer API that adds an event never breaks an older release.

## Errors

| class | raised when |
|---|---|
| `ValueError` | an invalid argument, before any request is sent (pydantic's `ValidationError`, a subclass, for out-of-range values); not a `CantiviaError` |
| `CantiviaError` | base class of everything below; also a stream that ended without a result, or a missing API key |
| `APIError` | a request failed |
| `APIConnectionError` | the API could not be reached, or the connection failed mid-response |
| `APITimeoutError` | the request timed out |
| `APIStatusError` | the API answered with an error status: `status_code`, `code`, `message`, `retry_after`, `body`, `request_id` |
| `BadRequestError` | 400 |
| `AuthenticationError` | 401 |
| `PaymentRequiredError` | 402 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `RequestTooLargeError` | 413 |
| `UnprocessableEntityError` | 422 |
| `RateLimitError` | 429 |
| `InternalServerError` | other 5xx |
| `ServiceUnavailableError` | 503 (a subclass of `InternalServerError`) |
| `VerificationError` | a result stream carried an error event: `message`, `request_id` |

`retry_after` is in seconds, set when the API sent `Retry-After`. `code` is the API's
machine-readable reason, the field to branch on.

`request_id` is the id the API gave the request, read from the `request-id` response
header (or the error body's `request_id`), and None when the response had none; an
`ErrorChunk` from a stream carries it too. The string form of both errors ends with it:

```
429 rate_limited: Rate limit exceeded. Retry in 5s. (request id req_8f14e45fceea467f9a1b2c3d4e5f6a7b)
```

Quote the request id when you contact support: it lets us find everything that happened
to that request.

## Constants

`DEFAULT_BASE_URL`, `DEFAULT_TIMEOUT`, `DEFAULT_MAX_RETRIES`, `MAX_CLAIM_CHARS`,
`MAX_IMAGE_B64_CHARS`, `MAX_POST_URL_CHARS`, `MAX_CATALOGUE_BATCH`, `MAX_BATCH_CLAIMS`,
`MAX_BATCH_CLAIM_CHARS`, `VERDICT_LABELS`,
`__version__`.

## Fact-checking (coming soon)

Fact-checking is not yet available on public plans, and calls to it are refused with
402 `no_entitlement` until it launches. The methods are already in the SDK, so code
written against them keeps working when it does. A refused call raises
`PaymentRequiredError` with `code` set to `no_entitlement`, or, on a stream, ends in an
error chunk saying fact-checking is unavailable.

Show people the verdict, the reasoning and the citations. Check `degraded` first: it
means the check could not run and `unverifiable` was filled in, which is not a judgment
about the claim, so try again rather than show it. A thorough fact-check can take
minutes; the `*_stream` forms yield each stage as it happens, including the live
reasoning text.

### Methods

| method | returns |
|---|---|
| `verify(claim, *, language="en", priority="standard", post_url=None)` (coming soon) | `VerificationResult` |
| `verify_stream(claim, *, language="en", priority="standard", post_url=None)` (coming soon) | iterator of `StreamChunk`, ending in `DoneChunk` |
| `verify_article(article, *, language="en", priority="standard")` (coming soon) | `ArticleVerificationResult` |
| `verify_article_stream(article, *, language="en", priority="standard")` (coming soon) | iterator of `StreamChunk`, ending in `ArticleDoneChunk` |
| `verify_image(image, *, language="en", priority="standard")` (coming soon) | `ArticleVerificationResult` |
| `verify_image_stream(image, *, language="en", priority="standard", forensics_only=False)` (coming soon; `forensics_only=True` is available today) | iterator of `StreamChunk`, ending in `ArticleDoneChunk` (or `MediaDoneChunk` with `forensics_only`) |
| `resolve(follow_up, *, prior_claims=None)` (coming soon) | `ResolveResult` |

- `claim: str` - one claim, 1 to 50,000 characters (`MAX_CLAIM_CHARS`).
- `article: str` - the document's text; URLs are not fetched.
- `image: str` - base64 image, a `data:` URI prefix accepted, up to 8,000,000 characters
  (`MAX_IMAGE_B64_CHARS`).
- `language: str` - the language of the input, e.g. `"en"`.
- `priority: Literal["standard", "high"]`.
- `post_url: str | None` - the social post the claim came from; with Titrate enabled the
  stream also carries a `TitrateChunk`.
- `follow_up: str` - 1 to 4,000 characters. `prior_claims: list[str] | None` - up to 20
  earlier claims of the conversation, never their verdicts.

`verify_article` splits the text into check-worthy claims, verifies each and returns an
overall verdict for the document; a link in it is checked as a link, not as the page
behind it. `verify_image` reads the text in a screenshot, meme or infographic and checks
it as a document. `resolve` turns a conversational follow-up such as "what about last
year?", which asserts nothing on its own, into a standalone claim, using the earlier
claims (never their verdicts).

`verify`, `verify_article` and `verify_image` raise `VerificationError` when the check
reports an error, and `CantiviaError` when the stream ends without a result.

### VerificationResult

| field | type | notes |
|---|---|---|
| `claim` | `str` | the claim checked |
| `verdict` | `Verdict \| str` | see [Verdicts](#verdicts) |
| `reasoning` | `str` | |
| `citations` | `list[Citation]` | `url`, `quote` |
| `sources` | `list[str] \| None` | every evidence URL found, a superset of those cited |
| `degraded` | `bool` | the check could not run and the verdict is a fallback; check it first |
| `cache_hit` | `bool` | |
| `route` | `str` | how the claim was handled |
| `processing_ms` | `float` | |
| `usage` | `RequestUsage \| None` | tokens consumed: `total_input_tokens`, `total_output_tokens`, and `by_model`, a breakdown under opaque keys that may change |
| `confidence` | `float` | a calibration signal for programmatic use; not a probability, do not show it to end users |

### ArticleVerificationResult

`claim_count`, `verified_count` (claims whose verdict carries truth signal),
`degraded_count` (claims the check failed to run on), `overall_verdict: Verdict | str`,
`summary`, `extraction_method`, `claims: list[VerificationResult]`, `processing_ms`, and
`overall_confidence` (programmatic use only; do not show it to end users).

### ResolveResult

`claim: str` (the rewrite, or the follow-up unchanged), `resolved: bool` (show the
rewrite only when true), `method: str`.

### Fact-check stream chunks

These join the chunks under [Stream chunks](#stream-chunks) in a fact-checking stream.

| `type` | class | carries |
|---|---|---|
| `routing` | `RoutingChunk` | `action`, `difficulty` |
| `cache_result` | `CacheResultChunk` | `hit`, `score`, `source` |
| `search_complete` | `SearchCompleteChunk` | `result_count`, `evidence_chars`, `sources: list[SearchSource]`, `degraded` |
| `factcheck_thinking` | `FactcheckThinkingChunk` | `delta`: live, unreviewed reasoning text |
| `factcheck_complete` | `FactcheckCompleteChunk` | `verdict` |
| `reasoning_complete` | `ReasoningCompleteChunk` | `verdict` |
| `debate_decomposed` | `DebateDecomposedChunk` | `sub_claims` |
| `debate_round` | `DebateRoundChunk` | `round`, `sub_claim_index` |
| `debate_argument` | `DebateArgumentChunk` | `role`, `content`, `round`, `sub_claim_index` |
| `done` | `DoneChunk` | `result: VerificationResult` |
| `ocr_complete` | `OcrCompleteChunk` | `text_chars`, `language` |
| `claims_extracted` | `ClaimsExtractedChunk` | `count`, `method`, `claims: list[ExtractedClaim]` |
| `claim_verified` | `ClaimVerifiedChunk` | `index`, `total`, `result: VerificationResult` |
| `article_done` | `ArticleDoneChunk` | `result: ArticleVerificationResult` |

### Batches

| method | returns |
|---|---|
| `submit_batch(claims, *, kind="claim", idempotency_key=None)` (coming soon) | `BatchAccepted` |
| `get_batch(batch_id)` (coming soon) | `Batch` |
| `wait_for_batch(batch_id, *, max_wait=3600, poll_interval=5, on_progress=None)` (coming soon) | `Batch` |

- `claims: list[str]` - 1 to 500 (`MAX_BATCH_CLAIMS`), each up to 10,000 characters
  (`MAX_BATCH_CLAIM_CHARS`).
- `kind: Literal["claim", "article"]`.
- `idempotency_key: str | None` - resubmitting with the same key returns the batch it
  first created; the same key with different claims raises `UnprocessableEntityError`.
- `batch_id: str` - the UUID `submit_batch` answered with; a malformed id raises
  `ValueError` before any request.

`wait_for_batch` raises `CantiviaError` naming the batch if it is unfinished after
`max_wait` seconds; the batch keeps running.

`BatchAccepted`: `batch_id`, `total`, `status`.

`Batch`: `batch_id`, `status: Literal["queued", "running", "completed", "failed"] | str`,
`total`, `pending`, `done`, `failed`, `created_at`, `finished_at`,
`items: list[BatchItem]`.

`BatchItem`: `idx`, `claim`, `status: Literal["pending", "done", "failed"] | str`, `verdict`,
`reasoning`, `citations`, `degraded`, `processing_ms`, `error`, and `confidence`
(programmatic use only).

### Verdicts

| verdict | meaning |
|---|---|
| `true` | every material element is affirmed by the evidence |
| `mostly_true` | accurate in substance; a peripheral detail is off |
| `partially_true` | some load-bearing elements hold, others do not |
| `misleading` | technically accurate and still leaves a false impression |
| `mostly_fake` | the core assertion fails; something incidental survives |
| `fake` | the core assertion is contradicted by the evidence |
| `unverifiable` | the evidence does not settle it, including claims not yet knowable |
| `not_a_claim` | the input was not a checkable claim (a greeting, a question) |

These are the verdicts today; treat any other value as unknown. `VERDICT_LABELS` maps
each `Verdict` to a label for display, such as "Mostly true";
`VERDICT_LABELS.get(verdict, verdict)` falls back to the raw value.
