from __future__ import annotations

import pytest

import cantivia.client as client_module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's own key or base URL must not leak into a test.
    monkeypatch.delenv("CANTIVIA_API_KEY", raising=False)
    monkeypatch.delenv("CANTIVIA_BASE_URL", raising=False)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every backoff and poll delay the client asked for, without waiting for any of them."""
    recorded: list[float] = []

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    async def fake_asleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    monkeypatch.setattr(client_module, "_asleep", fake_asleep)
    return recorded
