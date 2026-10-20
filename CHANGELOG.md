# Changelog

All notable changes to this package are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the package follows
[Semantic Versioning](https://semver.org); while the version is 0.x, a minor release
may break the API.

## 0.1.0

First public release.

- `CantiviaClient` and `AsyncCantiviaClient`, reading `CANTIVIA_API_KEY` and
  `CANTIVIA_BASE_URL` from the environment.
- Fact-checking: `verify`, `verify_article`, `verify_image`, each with a streaming form,
  and `resolve` for conversational follow-ups.
- The fact-checking methods, batches included, ship ahead of launch and are refused until then.
- Media forensics: `verify_media` and `verify_media_stream`.
- Media catalogue: `enroll_media`, `start_enrollment`, `get_enrollment_job`,
  `list_catalogue`, `delete_catalogue_asset`, `draft_notice`.
- Titrate: `score_post` and `score_post_stream`.
- Paper-mill screen: `screen_manuscript`.
- Batches: `submit_batch`, `get_batch`, `wait_for_batch`.
- `usage` and `health`.
- Typed errors for every status the API returns, automatic retries of requests that
  never reached the service, and configurable timeouts.
- Stream event types this release does not know are skipped, and a value it does not
  know in a known field (a new verdict, status or reason) is kept as its raw string, so
  a newer API does not break it.
- Every error carries `request_id`, the id of the failed request, to quote to support.
