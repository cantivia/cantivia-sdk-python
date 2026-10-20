# Contributing

Thank you for helping improve the Cantivia Python library. Issues and pull requests
are both welcome.

## Issues

Bug reports, questions and feature requests are welcome as GitHub issues. For a bug,
include the package version (`python -c "import cantivia; print(cantivia.__version__)"`),
your Python version, what you ran and what happened. Never paste an API key. Report
security issues privately as described in [SECURITY.md](SECURITY.md).

## Pull requests

Feel free to open a pull request: a bug fix, a clearer error message, a better
example, a missing type, a typo. For a larger change, open an issue first so we can
agree on the approach before you spend time on it.

Keep a change focused, add a test for it, and make sure the checks below pass.

This repository is published from Cantivia's internal source, so an accepted change is
applied there and arrives here in the next release. Your pull request is then closed
rather than merged, and the release notes credit you.

## Development

The project uses [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The tests run offline against a mocked transport; they need no API key and make no
network calls. To try the examples against the live API, set `CANTIVIA_API_KEY` and run
one, for example `uv run python examples/screen_manuscript.py "Title" abstract.txt`.

## Licence

By contributing you agree that your contribution is licensed under the Apache License,
Version 2.0, the licence of this project.
