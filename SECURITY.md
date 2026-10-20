# Security policy

## Reporting a vulnerability

Please report security issues privately to **security@cantivia.com**, not as a public
GitHub issue. Include what you found, how to reproduce it and the version affected.
We will acknowledge your report, keep you informed while we fix it, and credit you
if you wish.

This covers the `cantivia` package. Issues in the Cantivia API itself can be sent to
the same address.

## Supported versions

| version | supported |
|---|---|
| latest 0.x release | yes |
| older releases | no |

Security fixes are released as a new version; upgrade to the latest release to get them.

## Handling your API key

Keep your key in the `CANTIVIA_API_KEY` environment variable or a secrets manager, never
in source code. If a key is exposed, revoke it on your Cantivia dashboard and create a
new one.
