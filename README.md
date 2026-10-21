# Cantivia Python SDK

Typed Python client for the Cantivia API.

[![PyPI](https://img.shields.io/pypi/v/cantivia)](https://pypi.org/project/cantivia/)
[![Python](https://img.shields.io/pypi/pyversions/cantivia)](https://pypi.org/project/cantivia/)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](https://github.com/cantivia/cantivia-sdk-python/blob/main/LICENSE)

[Cantivia](https://cantivia.com) screens research manuscripts for paper-mill writing,
checks media for provenance, and scores how a social post is spreading. This library
gives Python applications synchronous and asynchronous access to its REST API.

## Products

| Product | What it does | Method |
|---|---|---|
| Paper-mill screen | Reads a manuscript's title and abstract and says whether an integrity reviewer should look at it | `screen_manuscript` |
| Media forensics | Checks an image, audio clip or video for C2PA provenance and for matches against media you enrolled | `verify_media` |
| Media catalogue | Enrols media you hold the rights to, and drafts takedown notices for copies found online | `enroll_media`, `draft_notice` |
| Titrate | Gives an early warning on whether a Bluesky post is about to spread widely | `score_post` |
| Fact-checking (coming soon) | A verdict, its reasoning and citations for a claim, article or image; refused with 402 `no_entitlement` until it launches | `verify`, `verify_article`, `verify_image` |

## Install

```sh
uv add cantivia
```

Python 3.10 or newer. The only dependencies are `httpx` and `pydantic`.

## Quick start

Create an API key on the [Cantivia dashboard](https://app.cantivia.com) and put it in the
`CANTIVIA_API_KEY` environment variable; the client reads it from there.

```python
from cantivia import CantiviaClient

with CantiviaClient() as client:
    screening = client.screen_manuscript(
        title="MicroRNA-21 promotes proliferation and invasion of gastric cancer cells by targeting PTEN",
        abstract="Objective: To investigate the role of miR-21 in gastric cancer. Methods: qRT-PCR, "
        "CCK-8 and Transwell assays were performed. Results: miR-21 promoted invasion by targeting PTEN.",
        budget=0.01,  # your reviewers can read one submission in a hundred
    )
    print(screening.decision)  # "review", "pass" or "unavailable"
    for caveat in screening.caveats:
        print("note:", caveat)
```

The screen scores writing style, not misconduct, so show the caveats with the decision.
`AsyncCantiviaClient` has the same methods as coroutines, and long checks have a
`*_stream` form that yields progress as it happens.

## Errors and retries

Every error the API, the network or a stream causes derives from `CantiviaError`; an
error status raises an `APIStatusError` subclass carrying `status_code`, a
machine-readable `code` and the `request_id` to quote to support. The client retries,
twice by default, only requests that provably never reached the service (a failed
connection, a 429 or a 503), never a timeout or another 5xx, so a check is never run or
billed twice.

## Documentation

- [API reference](https://github.com/cantivia/cantivia-sdk-python/blob/main/api.md): every method, parameter, return type, error and option, with retries, timeouts and streaming
- [Examples](https://github.com/cantivia/cantivia-sdk-python/tree/main/examples): a runnable script for each product
- [Changelog](https://github.com/cantivia/cantivia-sdk-python/blob/main/CHANGELOG.md): every release; the package follows [Semantic Versioning](https://semver.org), and while it is 0.x a minor release may break the API

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](https://github.com/cantivia/cantivia-sdk-python/blob/main/CONTRIBUTING.md).

## Security

Report a vulnerability privately as described in [SECURITY.md](https://github.com/cantivia/cantivia-sdk-python/blob/main/SECURITY.md).

## Licence

Apache-2.0. See [LICENSE](https://github.com/cantivia/cantivia-sdk-python/blob/main/LICENSE) and [NOTICE](https://github.com/cantivia/cantivia-sdk-python/blob/main/NOTICE).
