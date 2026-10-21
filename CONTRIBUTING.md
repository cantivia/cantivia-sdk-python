# Contributing to the Cantivia Python SDK

Thank you for helping improve the SDK. Issues and pull requests are both welcome, from a
typo or a clearer error message to a bug fix or a better example. If you are new here,
issues labelled
[good first issue](https://github.com/cantivia/cantivia-sdk-python/labels/good%20first%20issue)
are a good place to start.

## Report a bug

Open an issue with:

- the SDK version: `python -c "import cantivia; print(cantivia.__version__)"`
- your Python version and operating system
- a minimal snippet that shows the problem
- the request id from the error (`request id req_...`), which lets us find the request

Never paste an API key. Report security issues privately, as [SECURITY.md](SECURITY.md)
describes.

## Set up from source

The project uses [uv](https://docs.astral.sh/uv/) and supports Python 3.10 and later.

```sh
git clone https://github.com/<your-username>/cantivia-sdk-python
cd cantivia-sdk-python
uv sync --extra dev
```

## Format and lint

```sh
uv run ruff format .
uv run ruff check .
```

CI runs `ruff check` and `ruff format --check`, and a pull request has to pass both.

## Run and add tests

```sh
uv run pytest -q
```

The tests run offline: each one answers the SDK's requests with `httpx.MockTransport`, so
no API key is needed. A fix or a new method comes with a test in `tests/`, covering the
request it sends (method, path, body) and how the reply is parsed, including unknown
fields. CI runs the suite on every supported Python version.

To try a change against the live API, set `CANTIVIA_API_KEY` and run an example, such as
`uv run python examples/screen_manuscript.py "Title" abstract.txt`. Examples spend usage
on your account.

## Update the docs

- A change to public behaviour updates [api.md](api.md).
- Every user-visible change adds a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).
- The README changes only when the quick start does.

## Open a pull request

- Make one change, and keep it small. For anything larger, open an issue first so we can
  agree on the approach before you spend time on it.
- Title it as a plain sentence in the imperative: "Raise a clearer error when the API key
  is missing".
- Say what changed, why, and how you tested it, and link the issue it closes.

## Code style

- Match the code around your change.
- Type hints on every public function and model.
- Docstrings explain why, not what.
- The SDK depends on `httpx` and `pydantic` alone. Open an issue before adding a
  dependency.
- Never log or print an API key, even in an example.

## How a change ships

This repository is published from Cantivia's internal source. An accepted change is applied
there and ships in the next release; your pull request is then closed rather than merged,
and the release notes credit you.

## Licence

By contributing you agree that your contribution is licensed under the
[Apache License, Version 2.0](LICENSE), the licence of this project.
