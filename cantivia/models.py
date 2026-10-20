"""Pydantic models for the Cantivia API's requests, results and stream chunks.

Field aliases map the API's camelCase JSON (cacheHit, processingMs, resultCount)
onto snake_case Python attributes. `populate_by_name` keeps both spellings usable
when constructing models in code.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .errors import CantiviaError

_E = TypeVar("_E")

# A response field whose values the server may add to. A known value parses to its
# enum member or literal and anything else stays the raw string, because a published
# release cannot be changed and a value added later must not fail the whole parse.
# Compare against the known values; treat any other string as unknown. Requests stay
# strict.
OpenEnum = Annotated[_E | str, Field(union_mode="left_to_right")]

# Enumerations whose values the API defines, kept in one place so a revision is a
# one-line change. Product and model identifiers are plain strings for the same
# reason: treat them as opaque.
CacheHitSource = Literal["memory", "corpus"]


class Verdict(str, Enum):
    TRUE = "true"
    MOSTLY_TRUE = "mostly_true"
    PARTIALLY_TRUE = "partially_true"
    MISLEADING = "misleading"
    FAKE = "fake"
    MOSTLY_FAKE = "mostly_fake"
    UNVERIFIABLE = "unverifiable"
    # The input was not a checkable claim (a greeting, a question, a request for
    # something else). Distinct from UNVERIFIABLE, which means a real claim was
    # checked and could not be settled.
    NOT_A_CLAIM = "not_a_claim"


# TitleCase labels for display to end users. The snake_case enum value is the
# wire/storage form; these are the only place a human-facing spelling lives. Keyed by
# str so a verdict this release does not know can be looked up with
# ``VERDICT_LABELS.get(verdict, verdict)``.
VERDICT_LABELS: dict[str, str] = {
    Verdict.TRUE: "True",
    Verdict.MOSTLY_TRUE: "Mostly true",
    Verdict.PARTIALLY_TRUE: "Partially true",
    Verdict.MISLEADING: "Misleading",
    Verdict.FAKE: "Fake",
    Verdict.MOSTLY_FAKE: "Mostly fake",
    Verdict.UNVERIFIABLE: "Unverifiable",
    Verdict.NOT_A_CLAIM: "Not a claim",
}


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class Citation(_Model):
    url: str
    quote: str


# Cap on the text payload, matching the API. Sized for an article body rather than
# a single claim.
MAX_CLAIM_CHARS = 50_000


# Cap on a social post reference, matching the API.
MAX_POST_URL_CHARS = 2048


class VerificationRequest(_Model):
    claim: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS)
    language: str = "en"
    priority: Literal["standard", "high"] = "standard"
    # The Bluesky post the claim came from. When the server has Titrate enabled,
    # the stream then also carries a ``titrate`` chunk for that post.
    post_url: str | None = Field(default=None, max_length=MAX_POST_URL_CHARS, alias="postUrl")


class PostTitrateRequest(_Model):
    """A Titrate request (``kind: "post"``).

    Scores a social post's spread and nothing else: no fact-check runs, and the
    stream carries a single ``titrate`` chunk. ``post_url`` is a bsky.app post link
    or an ``at://`` post URI.
    """

    post_url: str = Field(min_length=1, max_length=MAX_POST_URL_CHARS, alias="postUrl")
    kind: Literal["post"] = "post"


class ArticleVerificationRequest(_Model):
    """A document verification request (``kind: "article"``).

    The server splits the text into check-worthy claims, verifies each
    independently, and aggregates them into one document verdict, so the terminal
    event is ``article_done``, not ``done``.

    ``claim`` carries the article *text*. The server does not fetch URLs, so
    passing a link verifies the link string rather than the page behind it.
    """

    claim: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS)
    kind: Literal["article"] = "article"
    language: str = "en"
    priority: Literal["standard", "high"] = "standard"


# Cap on a base64 media payload, matching the API: ~8 MB of base64, about a 6 MB file.
MAX_IMAGE_B64_CHARS = 8_000_000


class ImageVerificationRequest(_Model):
    """An image verification request (``kind: "image"``).

    The image's text is read and then checked like an article, so the terminal
    event is ``article_done``, not ``done``. ``image`` is base64 (a ``data:`` URI
    prefix is accepted by the server).
    """

    image: str = Field(min_length=1, max_length=MAX_IMAGE_B64_CHARS)
    kind: Literal["image"] = "image"
    language: str = "en"
    priority: Literal["standard", "high"] = "standard"
    # Media forensics alone: no OCR and no fact-check, and the stream ends in
    # ``media_done`` rather than ``article_done``.
    forensics_only: bool = Field(default=False, alias="forensicsOnly")


class AudioVerificationRequest(_Model):
    """An audio check (``kind: "audio"``): media forensics alone.

    The clip goes to C2PA provenance and a match against the account's own enrolled
    catalogue. No claim is checked, so the stream is one ``media_forensics`` chunk and
    then ``media_done``. ``media`` is base64 (a ``data:`` URI prefix is accepted).
    """

    media: str = Field(min_length=1, max_length=MAX_IMAGE_B64_CHARS)
    mime: str | None = Field(default=None, max_length=255)
    kind: Literal["audio"] = "audio"


class VideoVerificationRequest(_Model):
    """A video check (``kind: "video"``). See :class:`AudioVerificationRequest`."""

    media: str = Field(min_length=1, max_length=MAX_IMAGE_B64_CHARS)
    mime: str | None = Field(default=None, max_length=255)
    kind: Literal["video"] = "video"


# A batch is small because it is one request body, capped like a single image.
MAX_CATALOGUE_BATCH = 10


class CatalogueAsset(_Model):
    name: str = Field(min_length=1, max_length=255)
    # Base64 (a ``data:`` URI prefix is accepted).
    media: str = Field(min_length=1, max_length=MAX_IMAGE_B64_CHARS)


class CatalogueEnrollRequest(_Model):
    """Enrol media into the account's own catalogue.

    Later image, audio and video checks with any of the account's keys match copies
    of it; no other account sees it, and rotating a key keeps it.
    """

    assets: list[CatalogueAsset] = Field(min_length=1, max_length=MAX_CATALOGUE_BATCH)
    source: str = Field(default="api", max_length=100)


# How the catalogue names an asset: the lowercase hex SHA-256 of its bytes.
CATALOGUE_ASSET_ID_PATTERN = r"^[0-9a-f]{64}$"


class CatalogueJobAsset(_Model):
    name: str
    asset_id: str = Field(pattern=CATALOGUE_ASSET_ID_PATTERN, alias="assetId")
    # duplicate: the catalogue already held these exact bytes; nothing is billed.
    status: OpenEnum[Literal["pending", "enrolled", "duplicate", "failed"]]
    reason: str | None = None


class CatalogueEnrollJob(_Model):
    """An enrolment job: what enrolment answers at once (202), and as it runs.

    ``status`` goes queued -> running -> done, or failed when no asset was enrolled;
    done can still carry failed assets. ``gallery_size`` and ``max_assets`` are how
    many assets the catalogue holds and the most it can hold, None until an asset has
    been processed. Each receipt is signed and certifies ingestion, not authenticity.
    """

    job_id: str = Field(alias="jobId")
    status: OpenEnum[Literal["queued", "running", "done", "failed"]]
    total: int
    completed: int
    assets: list[CatalogueJobAsset]
    gallery_size: int | None = Field(default=None, alias="gallerySize")
    max_assets: int | None = Field(default=None, alias="maxAssets")
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class Complainant(_Model):
    name: str = Field(min_length=1, max_length=200)
    organisation: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    address: str = Field(default="", max_length=500)


class CatalogueNoticeRequest(_Model):
    """Draft a takedown notice for a copy found in the wild.

    A notice is drafted only if the copy matches the account's catalogue.
    """

    name: str = Field(min_length=1, max_length=255)
    media: str = Field(min_length=1, max_length=MAX_IMAGE_B64_CHARS)
    infringing_url: str = Field(max_length=2048, alias="infringingUrl")
    host: str = Field(default="", max_length=255)
    complainant: Complainant


class CatalogueNoticeResponse(_Model):
    """A drafted notice, or the reason none was drafted.

    ``status`` READY_FOR_REVIEW means a named person must check and approve the notice
    before anyone sends it; nothing has been sent. A miss is UNKNOWN and drafts nothing.
    """

    outcome: OpenEnum[Literal["MATCH", "UNKNOWN"]]
    status: OpenEnum[Literal["READY_FOR_REVIEW"]] | None = None
    notice: dict[str, Any] | None = None
    notice_text: str | None = Field(default=None, alias="noticeText")
    detail: str = ""


class CatalogueAssetRecord(_Model):
    """One asset the catalogue holds.

    Metadata only: the catalogue never keeps the uploaded bytes.
    ``asset_id`` is what :meth:`CantiviaClient.delete_catalogue_asset` takes.
    """

    asset_id: str = Field(pattern=CATALOGUE_ASSET_ID_PATTERN, alias="assetId")
    name: str
    modality: str
    enrolled_at: str = Field(alias="enrolledAt")
    source: str
    bytes_len: int = Field(alias="bytesLen")


class CatalogueListResponse(_Model):
    """What the catalogue holds, for the GDPR access right and to find the id to erase."""

    assets: list[CatalogueAssetRecord]
    gallery_size: int = Field(alias="gallerySize")
    max_assets: int = Field(alias="maxAssets")


class CatalogueDeleteResponse(_Model):
    """An erasure: the asset and everything the catalogue held for it were hard-deleted."""

    deleted: Literal[True]
    asset_id: str = Field(alias="assetId")
    gallery_size: int = Field(alias="gallerySize")
    max_assets: int = Field(alias="maxAssets")


class TokenUsage(_Model):
    """Token counts for one part of the work on one request.

    ``cache_read_tokens`` and ``cache_creation_tokens`` are input tokens reused from,
    or stored for, earlier work. ``context_window`` is the token limit that part ran
    with. Every field is optional: an absent field means "not reported", never zero.
    """

    input_tokens: int | None = Field(default=None, alias="inputTokens")
    output_tokens: int | None = Field(default=None, alias="outputTokens")
    cache_read_tokens: int | None = Field(default=None, alias="cacheReadTokens")
    cache_creation_tokens: int | None = Field(default=None, alias="cacheCreationTokens")
    context_window: int | None = Field(default=None, alias="contextWindow")


class RequestUsage(_Model):
    """Tokens one verification consumed, in total and broken down by part.

    The keys of ``by_model`` are opaque labels that may change between releases; sum
    them or ignore them, but do not branch on them. Only parts that report usage
    appear, so a missing part means it was not measured, not that it was free.
    """

    by_model: dict[str, TokenUsage] = Field(default_factory=dict, alias="byModel")
    total_input_tokens: int = Field(alias="totalInputTokens")
    total_output_tokens: int = Field(alias="totalOutputTokens")


class VerificationResult(_Model):
    """The verdict on one claim, with its reasoning and citations.

    Attributes are snake_case, but the API's JSON is camelCase. The default
    `model_dump()` / `model_dump_json()` emit the snake_case attribute names
    (`cache_hit`, `processing_ms`). To re-serialise into the API's wire format,
    pass `by_alias=True`, e.g. `result.model_dump(by_alias=True)`.
    """

    claim: str
    verdict: OpenEnum[Verdict]
    # A calibration signal for programmatic use; not a probability. Do not show it to end users.
    confidence: float
    reasoning: str
    citations: list[Citation] = Field(default_factory=list)
    # Every evidence URL the search found, a superset of the ones cited. May be absent.
    sources: list[str] | None = None
    cache_hit: bool = Field(alias="cacheHit")
    # How the claim was handled. Informational; the set of values may grow.
    route: str
    processing_ms: float = Field(alias="processingMs")
    # True when the verdict is a fallback rather than a judgment: the check could
    # not run, and "unverifiable" was filled in. Check it before showing a verdict.
    degraded: bool = False
    # Tokens the check consumed. None when not reported, and on a cache hit, which
    # consumes none.
    usage: RequestUsage | None = None


class ExtractedClaim(_Model):
    """One check-worthy claim pulled from a document (article mode).

    Decontextualised so it can be verified on its own. Wire fields are camelCase
    (checkWorthiness, claimType, originalText); attributes are snake_case.
    """

    claim: str
    check_worthiness: float = Field(alias="checkWorthiness")
    claim_type: OpenEnum[Literal["numerical", "event", "causal", "quote", "prediction", "definitional", "other"]] = (
        Field(alias="claimType")
    )
    original_text: str = Field(alias="originalText")
    entities: list[str] = Field(default_factory=list)
    verifiable: bool


class ArticleVerificationResult(_Model):
    """Aggregate result for an ``article`` request: each extracted claim's verdict
    plus a check-worthiness-weighted overall verdict for the document."""

    claim_count: int = Field(alias="claimCount")
    # How many of `claim_count` reached a verdict other than `unverifiable`. The
    # overall verdict is computed from these alone, so read it with this count: "nine
    # claims checked out false" and "eight unsettled, one false" can share a verdict.
    verified_count: int = Field(alias="verifiedCount")
    # How many claims could not be checked at all, as opposed to checked and left
    # unsettled. Each reads `unverifiable` with `degraded` set. A high count means the
    # document is worth checking again later, not that it is ambiguous.
    degraded_count: int = Field(alias="degradedCount")
    overall_verdict: OpenEnum[Verdict] = Field(alias="overallVerdict")
    # A calibration signal for programmatic use; not a probability. Do not show it to end users.
    overall_confidence: float = Field(alias="overallConfidence")
    summary: str
    extraction_method: str = Field(alias="extractionMethod")
    claims: list[VerificationResult] = Field(default_factory=list)
    processing_ms: float = Field(alias="processingMs")


class UsageRequests(_Model):
    """Request counts for the authenticated key."""

    window: int  # requests in the current sliding window (same window the limiter uses)
    today: int
    month: int


class UsageRateLimit(_Model):
    """Per-key rate-limit headroom."""

    limit: int
    remaining: int
    window_seconds: int


class UsageLatency(_Model):
    """Latency percentiles over the key's recent requests, in milliseconds."""

    p50: float
    p95: float
    samples: int


class UsageUnit(_Model):
    """One billable unit of a product, and how much of its allowance is used."""

    unit: str  # "claim", "media_check", "catalogue_item", "post_scored" or "manuscript_screened"
    # The calendar UTC month on a subscription, everything since the start on a
    # pilot, and all time for catalogue items held.
    used: int
    # None when the account holds no entitlement to the product, or holds an
    # Enterprise plan whose agreed limit has not been recorded.
    allowance: int | None = None
    # Units billed at API rates on top of the subscription. Always 0 on a pilot,
    # which is refused at its allowance instead.
    overage: int = 0


class UsageProduct(_Model):
    """The account's entitlement to one product."""

    # "factcheck", "praetorian" (media forensics), "titrate" or "papermill".
    product: str
    status: str  # "pilot", "active", "past_due", "cancelled" or "none"
    tier: str | None = None
    pilot_ends_at: str | None = None  # RFC 3339, set only on a pilot
    units: list[UsageUnit] = Field(default_factory=list)


class Usage(_Model):
    """Per-key usage returned by ``GET /api/v1/usage``.

    Scoped strictly to the calling API key, never global or cross-tenant data.
    The request, verdict and latency counters are approximate, expiring counters
    for usage insight. ``products`` is not: it is exact billing state, summed across
    every key on the account, because a pilot or a subscription is granted to the
    account rather than to one key.
    """

    key_id: str
    requests: UsageRequests
    rate_limit: UsageRateLimit
    verdicts: dict[str, int] = Field(default_factory=dict)
    latency_ms: UsageLatency
    products: list[UsageProduct] = Field(default_factory=list)


# Prior claims a follow-up is resolved against, and the follow-up's length cap.
MAX_PRIOR_CLAIMS = 20
MAX_FOLLOW_UP_CHARS = 4000


class ResolveRequest(_Model):
    """Rewrite a conversational follow-up into a claim that stands on its own.

    ``prior_claims`` are the earlier claims of the conversation, never their verdicts:
    a rewrite that could see the answers might fold one in, and then a sentence the
    user never asserted would be checked.
    """

    prior_claims: list[str] = Field(default_factory=list, max_length=MAX_PRIOR_CLAIMS, alias="priorClaims")
    follow_up: str = Field(min_length=1, max_length=MAX_FOLLOW_UP_CHARS, alias="followUp")


class ResolveResult(_Model):
    """A follow-up resolved into a standalone claim, ready to pass to ``verify``.

    ``claim`` is the rewrite, or the caller's own words when nothing was rewritten.
    Show the user the rewritten claim only when ``resolved`` is true; it is false
    whenever nothing was rewritten, including when resolution failed.
    """

    claim: str
    resolved: bool
    # How the answer was reached ("passthrough", "model", "unparsed" or "error").
    method: str


MAX_MANUSCRIPT_TITLE_CHARS = 2000
MAX_MANUSCRIPT_ABSTRACT_CHARS = 20_000


class PaperMillScreenRequest(_Model):
    """Screen one manuscript's title and abstract for paper-mill style.

    At least one of ``title`` and ``abstract`` must hold text. ``budget`` is the flag
    rate your integrity office can staff, e.g. 0.01 for one submission in a hundred:
    only bands at or under it can flag. Omitted, a manuscript is flagged if it clears
    any of the screen's bands.
    """

    title: str = Field(default="", max_length=MAX_MANUSCRIPT_TITLE_CHARS)
    abstract: str = Field(default="", max_length=MAX_MANUSCRIPT_ABSTRACT_CHARS)
    budget: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _has_text(self) -> PaperMillScreenRequest:
        if not self.title.strip() and not self.abstract.strip():
            raise ValueError("a title or an abstract is required")
        return self


class ScreeningModel(_Model):
    """Which version of the screen answered, so a screening can be traced to it.

    ``cohort`` names the collection of papers it was trained on, ``trained_through`` is
    the last publication year in that collection, and ``trained_on`` holds counts
    describing it, such as how many papers it held.
    """

    cohort: str
    trained_through: float = Field(alias="trainedThrough")
    trained_on: dict[str, float] = Field(default_factory=dict, alias="trainedOn")


class PaperMillScreenResult(_Model):
    """The paper-mill screen's answer for one manuscript.

    ``decision`` is ``review`` (a person should read this), ``pass`` (no reason to)
    or ``unavailable`` (the screen could not run). There is deliberately no answer
    meaning "fraudulent" or "clean": the screen scores writing style, and a
    legitimate paper written in the genre's formula can score like a mill product.
    """

    decision: OpenEnum[Literal["review", "pass", "unavailable"]]
    # None when the manuscript holds no term the screen was trained on: an unreadable
    # score, not a low one.
    score: float | None = None
    # The flag rate this manuscript cleared, and the precision measured at it on
    # held-out papers, with its interval. The two always travel together.
    band: float | None = None
    precision: float | None = None
    precision_interval: tuple[float, float] | None = Field(default=None, alias="precisionInterval")
    # The terms that pushed the score up, with their weights, so an editor can
    # interrogate the flag rather than trust it.
    evidence: list[tuple[str, float]] = Field(default_factory=list)
    # What the score does not mean. Show these with the decision.
    caveats: list[str] = Field(default_factory=list)
    model: ScreeningModel | None = None
    error: str | None = None


# A batch is one request body, so it is bounded: at most this many claims, each at
# most this long. Article batches take article text under the same per-item cap.
MAX_BATCH_CLAIMS = 500
MAX_BATCH_CLAIM_CHARS = 10_000


class BatchRequest(_Model):
    """Verify many claims (or articles) in the background."""

    claims: list[Annotated[str, Field(min_length=1, max_length=MAX_BATCH_CLAIM_CHARS)]] = Field(
        min_length=1, max_length=MAX_BATCH_CLAIMS
    )
    kind: Literal["claim", "article"] = "claim"


class BatchAccepted(_Model):
    """A submitted batch: what ``submit_batch`` answers at once (the API answers 202)."""

    batch_id: str
    total: int
    status: OpenEnum[Literal["queued", "running", "completed", "failed"]]


class BatchItem(_Model):
    """One claim of a batch and, once it has run, its outcome."""

    idx: int
    claim: str
    status: OpenEnum[Literal["pending", "done", "failed"]]
    # A Verdict value once the item is done.
    verdict: str | None = None
    # A calibration signal for programmatic use; not a probability. Do not show it to end users.
    confidence: float | None = None
    reasoning: str | None = None
    citations: list[Citation] | None = None
    # True when the verdict is a fallback rather than a judgment (see VerificationResult).
    degraded: bool | None = None
    processing_ms: int | None = None
    # Why the item failed, when it did.
    error: str | None = None


class Batch(_Model):
    """A batch's progress and results.

    ``status`` goes queued -> running -> completed, or failed. A completed batch can
    still hold failed items: read each item's ``status`` and ``error``.
    """

    batch_id: str
    status: OpenEnum[Literal["queued", "running", "completed", "failed"]]
    total: int
    pending: int
    done: int
    failed: int
    created_at: str
    finished_at: str | None = None
    items: list[BatchItem] = Field(default_factory=list)


class RoutingChunk(_Model):
    """The check has started. Informational: ``action`` names how the claim will be
    handled, and ``difficulty`` is how hard it was judged to be, from 0 to 1."""

    type: Literal["routing"]
    action: str
    difficulty: float


class CacheResultChunk(_Model):
    """Whether an earlier check of the same claim answered this one."""

    type: Literal["cache_result"]
    hit: bool
    score: float | None = None
    # Which cache answered a hit. Informational, and None when absent.
    source: OpenEnum[CacheHitSource] | None = None


class SearchSource(_Model):
    """A single evidence source surfaced by web search."""

    title: str
    url: str


class SearchCompleteChunk(_Model):
    type: Literal["search_complete"]
    result_count: int = Field(alias="resultCount")
    # Character length of the evidence gathered, and the sources it came from. Either
    # may be absent.
    evidence_chars: int | None = Field(default=None, alias="evidenceChars")
    sources: list[SearchSource] | None = None
    # True when no usable evidence was found; the verdict then comes back
    # "unverifiable" without a fact-check. False when absent.
    degraded: bool = False


class FactcheckThinkingChunk(_Model):
    """Live progress text from the check, streamed as it is written.

    Progress, not a verdict: the text is unreviewed, and the verdict that follows
    can disagree with it.
    """

    type: Literal["factcheck_thinking"]
    delta: str


class FactcheckCompleteChunk(_Model):
    type: Literal["factcheck_complete"]
    verdict: OpenEnum[Verdict]
    # A calibration signal for programmatic use; not a probability. Do not show it to end users.
    confidence: float


class ReasoningCompleteChunk(_Model):
    type: Literal["reasoning_complete"]
    verdict: OpenEnum[Verdict]


class DoneChunk(_Model):
    type: Literal["done"]
    result: VerificationResult


class ErrorChunk(_Model):
    type: Literal["error"]
    message: str
    # Set by the client from the stream's ``request-id`` header, so an error seen
    # mid-stream can be quoted to support. Not part of the event itself.
    request_id: str | None = None


class OcrCompleteChunk(_Model):
    """Image mode: the image's text has been read, and is checked like an article
    next. Reports how much text was found and its language."""

    type: Literal["ocr_complete"]
    text_chars: int = Field(alias="textChars")
    language: str


# Media forensics answers "was this file made or altered by a known process?", a
# different question about a different object from the claim verdict. There is no
# "real" member and there must never be one: "unknown" means no enabled instrument
# found anything, not that the file is authentic.
MediaForensicsVerdict = Literal["synthetic", "unknown"]


def _never_real(value: str) -> str:
    # Open like every other response enum, except that "real" fails the parse: a
    # server that sent it is broken, and the value must never reach a caller to show.
    if value == "real":
        raise ValueError('a media forensics verdict is never "real"')
    return value


_OpenMediaForensicsVerdict = Annotated[OpenEnum[MediaForensicsVerdict], AfterValidator(_never_real)]


class MediaForensicsChannel(_Model):
    """One instrument's reading of the file.

    Render ``scope``, ``caveats`` and ``detail`` with the verdict: without them the
    verdict claims more than the instrument measured.
    """

    channel: str
    verdict: _OpenMediaForensicsVerdict
    # A statistical signal for programmatic use. Do not show it to end users.
    p_value: float | None = Field(alias="pValue")
    coverage_valid: bool = Field(alias="coverageValid")
    scope: str
    caveats: list[str] = Field(default_factory=list)
    # What the instrument saw, in words — including why a no-match is a no-match.
    # Empty when absent.
    detail: str = ""
    # The strongest grade a positive from this channel can carry, and the scope
    # statements the answer rests on.
    grade_ceiling: str | None = Field(default=None, alias="gradeCeiling")
    scope_caveats: list[str] = Field(default_factory=list, alias="scopeCaveats")
    # Catalogue matching only: how many assets the catalogue held when this check ran.
    gallery_size: int | None = Field(default=None, alias="gallerySize")
    # Retrieval matches only: the sealed chain-of-custody package for the match.
    evidence: dict[str, Any] | None = None


class MediaForensicsUnavailable(_Model):
    """A channel that was switched on but could not run, and why."""

    channel: str
    reason: str


class MediaForensicsResult(_Model):
    """The adjudicated outcome over every channel that ran."""

    verdict: _OpenMediaForensicsVerdict
    method: OpenEnum[Literal["hard", "sidak", "simes", "none"]]
    # A statistical signal for programmatic use. Do not show it to end users.
    combined_p: float | None = Field(alias="combinedP")
    channels: list[MediaForensicsChannel] = Field(default_factory=list)
    excised: list[str] = Field(default_factory=list)
    alpha: float
    unavailable: list[MediaForensicsUnavailable] = Field(default_factory=list)


class MediaForensicsChunk(_Model):
    """The media-forensics panel finished analysing the submitted file.

    Independent of any claim verdict in the same stream. Emitted only when a channel
    ran, so its absence means forensics was off, never that it found nothing.
    """

    type: Literal["media_forensics"]
    modality: OpenEnum[Literal["image", "audio", "video"]]
    result: MediaForensicsResult


class MediaDoneChunk(_Model):
    """Audio and video mode terminal event: the ``media_forensics`` chunk before it is the answer."""

    type: Literal["media_done"]
    modality: OpenEnum[Literal["image", "audio", "video"]]


class ClaimsExtractedChunk(_Model):
    """Article mode: the document was split into these check-worthy claims."""

    type: Literal["claims_extracted"]
    count: int
    method: str
    claims: list[ExtractedClaim]


class ClaimVerifiedChunk(_Model):
    """Article mode: one extracted claim finished verifying (streamed as it lands)."""

    type: Literal["claim_verified"]
    index: int
    total: int
    result: VerificationResult


class ArticleDoneChunk(_Model):
    """Article mode terminal event (the per-document analogue of ``done``)."""

    type: Literal["article_done"]
    result: ArticleVerificationResult


class DebateDecomposedChunk(_Model):
    """Debate: the claim was split into these sub-claims, each argued on its own."""

    type: Literal["debate_decomposed"]
    sub_claims: list[str] = Field(alias="subClaims")


class DebateRoundChunk(_Model):
    """Debate: a new round of argument began for a sub-claim."""

    type: Literal["debate_round"]
    round: int
    sub_claim_index: int = Field(alias="subClaimIndex")


class DebateArgumentChunk(_Model):
    """Debate: one argument in a round is complete. ``role`` names the position it argued.

    Progress, not a verdict: the verdict that follows can disagree with any argument.
    """

    type: Literal["debate_argument"]
    role: str
    content: str
    # A calibration signal for programmatic use; not a probability. Do not show it to end users.
    confidence: float
    round: int
    sub_claim_index: int = Field(alias="subClaimIndex")


class TitrateAbstainReason(str, Enum):
    """Why a post was not scored. Most live posts are not: most get no reply at all.

    ``Titrate.detail`` says the same in words. The four "too little activity" reasons
    differ only in which check found it; treat them alike.
    """

    # No post was given.
    NO_POST_REFERENCE = "no_post_reference"
    # The link is not a Bluesky post.
    UNSUPPORTED_URL = "unsupported_url"
    # Posts from this platform cannot be scored yet.
    NO_ARTIFACT_FOR_PLATFORM = "no_artifact_for_platform"
    # The post was deleted, blocked or is private.
    POST_NOT_FOUND = "post_not_found"
    # The post is a reply or a quote; score the post that started the thread.
    NOT_A_ROOT = "not_a_root"
    # The thread could not be fetched just now; try again.
    FETCH_FAILED = "fetch_failed"
    # Nobody has replied to the post.
    NO_REPLIES = "no_replies"
    # The post is under an hour old; try again once its first hour has passed.
    WINDOW_OPEN = "window_open"
    # Too little activity in the post's first hour to call it.
    TOO_LITTLE_TO_FEATURISE = "too_little_to_featurise"
    BELOW_MIN_WINDOW_NODES = "below_min_window_nodes"
    PREFIX_SHORTER_THAN_K = "prefix_shorter_than_k"
    NO_FEATURISABLE_WINDOW = "no_featurisable_window"
    # The thread could not be read.
    MALFORMED_THREAD = "malformed_thread"
    # The post could not be scored.
    UNSCORABLE = "unscorable"
    # Titrate is unavailable; try again later.
    UNAVAILABLE = "unavailable"


class SizeRange(_Model):
    """The final thread size, root included, that ``level`` of posts like this land inside."""

    low: int
    high: int
    level: float


class Titrate(_Model):
    """A social post's early-warning answer from Titrate.

    It scores spread, not truth. ``probability`` is a signal for programmatic use:
    do not show it to end users. Show ``is_viral``, the thread size it refers to
    (``viral_threshold_size``), ``size_range`` and ``abstain_reason``.
    """

    scored: bool
    abstain_reason: OpenEnum[TitrateAbstainReason] | None = Field(default=None, alias="abstainReason")
    detail: str | None = None
    # Only Bluesky posts are scored today; "reddit" is reserved.
    platform: OpenEnum[Literal["bluesky", "reddit"]] | None = None
    post_uri: str | None = Field(default=None, alias="postUri")
    probability: float | None = None
    # ``probability`` against a threshold frozen into the model before any live run.
    is_viral: bool | None = Field(default=None, alias="isViral")
    decision_threshold: float | None = Field(default=None, alias="decisionThreshold")
    viral_threshold_size: float | None = Field(default=None, alias="viralThresholdSize")
    horizon_hours: float | None = Field(default=None, alias="horizonHours")
    # The thread as fetched, and the part inside the window. A thread already past
    # ``viral_threshold_size`` is being described, not forecast.
    observed_nodes: int | None = Field(default=None, alias="observedNodes")
    nodes_in_window: int | None = Field(default=None, alias="nodesInWindow")
    elapsed_hours: float | None = Field(default=None, alias="elapsedHours")
    extrapolated: bool | None = None
    # None when not scored, or when the thread has already outgrown it.
    size_range: SizeRange | None = Field(default=None, alias="sizeRange")
    # Which scorer answered, and the exact version of it (a hash and a format
    # number), so an answer can be traced to the scorer that gave it.
    model: str
    artifact_sha256: str | None = Field(default=None, alias="artifactSha256")
    artifact_format_version: int | None = Field(default=None, alias="artifactFormatVersion")


class TitrateChunk(_Model):
    """Titrate: a ``post`` request, or a claim request that carried ``post_url``."""

    type: Literal["titrate"]
    result: Titrate


StreamChunk = Annotated[
    RoutingChunk
    | CacheResultChunk
    | SearchCompleteChunk
    | FactcheckThinkingChunk
    | FactcheckCompleteChunk
    | ReasoningCompleteChunk
    | DoneChunk
    | OcrCompleteChunk
    | MediaForensicsChunk
    | MediaDoneChunk
    | ClaimsExtractedChunk
    | ClaimVerifiedChunk
    | ArticleDoneChunk
    | DebateDecomposedChunk
    | DebateRoundChunk
    | DebateArgumentChunk
    | TitrateChunk
    | ErrorChunk,
    Field(discriminator="type"),
]

_chunk_adapter: TypeAdapter[StreamChunk] = TypeAdapter(StreamChunk)


def parse_chunk(data: str) -> StreamChunk | None:
    """Parse one SSE `data:` payload into the matching typed stream chunk.

    Returns None for an event type this release does not know. The stream methods
    skip those, so a newer API that adds an event never breaks this version.

    Raises:
        CantiviaError: the payload is malformed JSON, or a known chunk type with
            fields that do not match it (wrapping the underlying pydantic
            ``ValidationError`` so callers never have to catch it).
    """
    try:
        return _chunk_adapter.validate_json(data)
    except ValidationError as exc:
        errors = exc.errors()
        if len(errors) == 1 and errors[0]["type"] == "union_tag_invalid":
            return None
        raise CantiviaError(f"unrecognised chunk from server: {data[:200]}") from exc
