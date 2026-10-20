# Cantivia Python API library

Cantivia checks images, audio and video for C2PA provenance and for matches against
media you have enrolled, scores how a social post is spreading, and screens research
manuscripts for paper-mill writing. Checking claims, articles and images against
evidence, with a verdict, its reasoning and citations, is coming soon: see
[Fact-checking (coming soon)](#fact-checking-coming-soon). This library gives Python
3.10+ applications typed, synchronous and asynchronous access to the Cantivia REST API.

The full list of methods, parameters and return types is in [api.md](api.md).

## Installation

```sh
uv add cantivia
```

The package depends only on `httpx` and `pydantic`.

## Usage

Create an API key on your Cantivia dashboard and put it in the `CANTIVIA_API_KEY`
environment variable. The client reads it from there, so it never needs to sit in
your source code.

```python
from cantivia import CantiviaClient

client = CantiviaClient()  # reads CANTIVIA_API_KEY

screening = client.screen_manuscript(
    "MicroRNA-21 promotes proliferation and invasion of gastric cancer cells by targeting PTEN",
    "Objective: To investigate the role of miR-21 in gastric cancer. Methods: qRT-PCR, CCK-8 and Transwell "
    "assays were performed. Results: miR-21 was upregulated and promoted proliferation, migration and invasion "
    "by targeting PTEN. Conclusion: miR-21 may be a novel therapeutic target.",
    budget=0.01,
)

print(screening.decision)  # "review", "pass" or "unavailable"
for caveat in screening.caveats:
    print("note:", caveat)
```

The screen reads a manuscript's title and abstract and says whether an integrity
reviewer should look at it. It scores writing style, not misconduct, so show the
`caveats` with the decision. `unavailable` means the screen could not run, and `error`
says why.

Use the client as a context manager, or call `client.close()` when you are done, so
its connections are released.

## Async usage

`AsyncCantiviaClient` has the same methods as coroutines:

```python
import asyncio

from cantivia import AsyncCantiviaClient


async def main() -> None:
    async with AsyncCantiviaClient() as client:
        screening = await client.screen_manuscript(
            "Effects of a school breakfast programme on attendance",
            "We compared attendance across 40 schools before and after a breakfast programme was introduced.",
            budget=0.01,
        )
        print(screening.decision)


asyncio.run(main())
```

## Streaming

Long-running checks have a `*_stream` form that yields typed chunks as the work
progresses. `verify_media_stream` yields a `MediaForensicsChunk` carrying the result,
then a `MediaDoneChunk`; `score_post_stream` yields one `TitrateChunk` carrying the
answer. A failure arrives as an `ErrorChunk`:

```python
import base64

from cantivia import CantiviaClient, ErrorChunk, MediaDoneChunk, MediaForensicsChunk

with CantiviaClient() as client:
    clip = base64.b64encode(open("clip.mp4", "rb").read()).decode()
    for chunk in client.verify_media_stream(clip, "video", mime="video/mp4"):
        if isinstance(chunk, MediaForensicsChunk):
            print("verdict:", chunk.result.verdict)
        elif isinstance(chunk, MediaDoneChunk):
            print("done")
        elif isinstance(chunk, ErrorChunk):
            print("failed:", chunk.message)
```

With `AsyncCantiviaClient`, iterate with `async for`. Streaming is the recommended way
to run long work: progress arrives as it happens, rather than after one long wait. An
event type this version of the library does not know is skipped, so a newer API never
breaks an older release.

## Products

### Media forensics

`verify_media` checks an image, audio clip or video for C2PA provenance and matches it
against media your account has enrolled. The verdict is `synthetic` or `unknown`, never
"real": `unknown` means nothing was found, not that the file is authentic.

```python
import base64

with CantiviaClient() as client:
    clip = base64.b64encode(open("clip.mp4", "rb").read()).decode()
    result = client.verify_media(clip, "video", mime="video/mp4")
    print(result.verdict)
    for channel in result.channels:
        print(channel.channel, channel.verdict, channel.detail)
        for caveat in channel.caveats:
            print("  caveat:", caveat)
```

### Media catalogue

Enrol media you hold the rights to, and later checks by any key on your account match
copies of it. Only your account sees it.

```python
with CantiviaClient() as client:
    photo = base64.b64encode(open("press-photo.jpg", "rb").read()).decode()
    job = client.enroll_media([{"name": "press-photo.jpg", "media": photo}])
    for asset in job.assets:
        print(asset.name, asset.status, asset.reason or "")

    listing = client.list_catalogue()
    print(f"{listing.gallery_size} of {listing.max_assets} assets held")
```

`draft_notice` drafts a takedown notice for a copy found online, for a person to review
before anything is sent; `delete_catalogue_asset` erases an asset.

### Titrate: how a post is spreading

`score_post` gives an early warning on whether a social post's thread will grow large.
It scores spread, not truth, and often declines to score: most posts get no replies.

```python
with CantiviaClient() as client:
    answer = client.score_post("https://bsky.app/profile/example.com/post/3kabc")
    if answer.scored:
        print("likely to spread" if answer.is_viral else "not likely to spread")
    else:
        print("not scored:", answer.abstain_reason.value if answer.abstain_reason else "unknown")
```

### Paper-mill screen

`screen_manuscript` reads a manuscript's title and abstract and says whether an
integrity reviewer should look at it. It scores writing style, not misconduct.

```python
with CantiviaClient() as client:
    screening = client.screen_manuscript(title, abstract, budget=0.01)
    print(screening.decision)  # "review", "pass" or "unavailable"
    if screening.decision == "review":
        print("precision at this flag rate:", screening.precision)
        for term, weight in screening.evidence:
            print(" ", term, weight)
```

`budget` is the share of submissions your reviewers can read, here one in a hundred.

### Usage

```python
with CantiviaClient() as client:
    usage = client.usage()
    print(usage.requests.month, "requests this month")
    print(usage.rate_limit.remaining, "of", usage.rate_limit.limit, "left in this window")
    for product in usage.products:
        print(product.product, product.status)
```

## Handling errors

Every error the API, the network or a result stream causes derives from
`cantivia.CantiviaError`. An invalid argument (an empty claim, a budget outside 0 to 1,
a malformed id) raises `ValueError` before any request is sent; for out-of-range values
it is pydantic's `ValidationError`, a `ValueError` subclass.

When the API cannot be reached, `APIConnectionError` is raised; a timeout raises its
subclass `APITimeoutError`. When the API answers with an error status, a subclass of
`APIStatusError` is raised, carrying `status_code`, the API's machine-readable `code`,
its `message`, `retry_after` (seconds, when the API sent `Retry-After`), the `body` and
the `request_id`.

```python
import cantivia

client = cantivia.CantiviaClient()

try:
    client.screen_manuscript(title, abstract)
except cantivia.RateLimitError as e:
    print("rate limited; retry after", e.retry_after, "seconds")
except cantivia.APIStatusError as e:
    print(e.status_code, e.code, e.message, e.request_id)
except cantivia.APIConnectionError:
    print("could not reach the API")
```

| status | error |
|---|---|
| 400 | `BadRequestError` |
| 401 | `AuthenticationError` |
| 402 | `PaymentRequiredError` |
| 403 | `PermissionDeniedError` |
| 404 | `NotFoundError` |
| 409 | `ConflictError` |
| 413 | `RequestTooLargeError` |
| 422 | `UnprocessableEntityError` |
| 429 | `RateLimitError` |
| 503 | `ServiceUnavailableError` |
| other 5xx | `InternalServerError` |
| no response | `APIConnectionError`, `APITimeoutError` |

A result stream can also end in an error event instead of a result; the non-streaming
methods raise that as `VerificationError`.

Every response carries a `request-id` header, and both `APIStatusError` and
`VerificationError` expose it as `request_id` (an `ErrorChunk` from a stream carries
it too). It is also shown when the error is printed:

```
429 rate_limited: Rate limit exceeded. Retry in 5s. (request id req_8f14e45fceea467f9a1b2c3d4e5f6a7b)
```

Quote the request id when you contact support: it lets us find everything that
happened to that request.

## Retries

The client retries only requests that provably never reached the service: a connection
that could not be opened (refused, reset while connecting, DNS failure, connect
timeout), `429 Too Many Requests` and `503 Service Unavailable`. Both statuses are
refusals made before any work starts. Once a request has been sent it is never retried:
not after a timeout, a dropped connection, any other 5xx or 4xx, or a stream that has
started, because the work may already be under way and would run, and be billed, twice.

It waits as long as the API's `Retry-After` header asks, up to 60 seconds, and otherwise
backs off exponentially from 0.5 s to at most 8 s with jitter. The default is 2 retries;
set `max_retries` on the client or on one call, and 0 turns retrying off:

```python
client = CantiviaClient(max_retries=0)
client.screen_manuscript(title, abstract, max_retries=5)
```

## Timeouts

A request times out when it goes 600 s without data, by default, with 10 seconds to
connect. A stream counts every event as data, so a long check that keeps streaming
progress does not time out. Pass a number of seconds or an `httpx.Timeout`, on the
client or on one call:

```python
import httpx

client = CantiviaClient(timeout=120.0)
client.screen_manuscript(title, abstract, timeout=httpx.Timeout(900.0, connect=5.0))
```

A timeout raises `APITimeoutError` and is not retried.

## Configuration

| option | environment variable | default |
|---|---|---|
| `api_key` | `CANTIVIA_API_KEY` | none; required for everything but `health()` |
| `base_url` | `CANTIVIA_BASE_URL` | `https://api.cantivia.com` |
| `timeout` | | 600 s without data, 10 s to connect |
| `max_retries` | | 2 |
| `http_client` | | a new `httpx.Client` |

Pass your own `httpx.Client` (or `httpx.AsyncClient`) as `http_client` to set a proxy or
custom transport. The options above still apply to every request.

Each request sends `User-Agent: cantivia-python/<version>`.

## Fact-checking (coming soon)

Fact-checking is not yet available on public plans, and calls to it are refused with
402 `no_entitlement` until it launches. The methods are already in this library, so
code written against them keeps working when it does.

A refused call raises `PaymentRequiredError` with `code` set to `no_entitlement`, or,
on a stream, ends in an error chunk saying fact-checking is unavailable.

### Fact-check a claim

```python
from cantivia import CantiviaClient

client = CantiviaClient()  # reads CANTIVIA_API_KEY

result = client.verify(
    "The Health Service Executive said hospital waiting lists fell by 12% in the first quarter of 2026."
)

# `degraded` means the check could not run and "unverifiable" was filled in. It is
# not a judgment about the claim, so check it before showing a verdict to anyone.
if result.degraded:
    print("The check did not run; try again later.")
else:
    print(result.verdict.value)
    print(result.reasoning)
    for citation in result.citations:
        print(citation.url, "-", citation.quote)
```

Show people the verdict, the reasoning and the citations. Results also carry a
`confidence` field: it is an internal signal for programmatic use, not a
probability, and should not be shown to end users.

`AsyncCantiviaClient` has the same methods as coroutines, for example
`await client.verify(...)`.

### Streaming a fact-check

A thorough fact-check can take minutes. Every check has a `*_stream` form that yields
typed chunks as the work progresses, ending in a chunk that carries the result:

```python
from cantivia import CantiviaClient, DoneChunk, ErrorChunk, FactcheckThinkingChunk

with CantiviaClient() as client:
    for chunk in client.verify_stream("The Eiffel Tower was moved to Berlin in 2025."):
        if isinstance(chunk, FactcheckThinkingChunk):
            print(chunk.delta, end="", flush=True)
        elif isinstance(chunk, DoneChunk):
            print("\nverdict:", chunk.result.verdict.value)
        elif isinstance(chunk, ErrorChunk):
            print("\nfailed:", chunk.message)
```

### Fact-check an article

Pass the article's text. The API splits it into check-worthy claims, verifies each one
and returns an overall verdict for the document. It does not fetch URLs, so a link is
checked as a link, not as the page behind it.

```python
with CantiviaClient() as client:
    article = open("article.txt", encoding="utf-8").read()
    result = client.verify_article(article)
    print(result.overall_verdict.value, "-", result.summary)
    print(f"{result.verified_count} of {result.claim_count} claims settled, {result.degraded_count} not checked")
    for claim in result.claims:
        print(claim.verdict.value, claim.claim)
```

### Fact-check an image

The text in a screenshot, meme or infographic is read and checked as a document:

```python
import base64

with CantiviaClient() as client:
    image = base64.b64encode(open("screenshot.png", "rb").read()).decode()
    result = client.verify_image(image)
    print(result.overall_verdict.value, result.claim_count)
```

### Batches

Submit up to 500 claims to verify in the background, then wait for the results:

```python
with CantiviaClient() as client:
    accepted = client.submit_batch(claims, idempotency_key="newsletter-2026-10-20")
    batch = client.wait_for_batch(accepted.batch_id)
    for item in batch.items:
        print(item.idx, item.status, item.verdict or item.error)
```

An `idempotency_key` makes resubmitting safe: the same key returns the batch it first
created instead of starting a second one.

### Follow-up questions

In a conversation, "what about last year?" asserts nothing on its own. `resolve` turns
it into a standalone claim, using the earlier claims (never their verdicts):

```python
with CantiviaClient() as client:
    resolution = client.resolve("What about last year?", prior_claims=["Irish inflation fell in 2026."])
    if resolution.resolved:
        result = client.verify(resolution.claim)
```

### What the verdicts mean

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

These are the verdicts today; treat any other value as unknown. A newer server can add
one, and this release passes it through as its raw string. `VERDICT_LABELS` maps each
`Verdict` to a label for display, such as "Mostly true";
`VERDICT_LABELS.get(verdict, verdict)` falls back to the raw value.

## Requirements

Python 3.10 or newer.

## Versioning

This package follows [Semantic Versioning](https://semver.org). While the version is
0.x, a minor release may contain breaking changes; each is listed in
[CHANGELOG.md](CHANGELOG.md).

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), and
[SECURITY.md](SECURITY.md) for reporting a vulnerability.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
