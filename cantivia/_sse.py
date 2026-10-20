"""Minimal Server-Sent Events parser.

Only `data:` fields carry anything; `event:`, `id:`, `retry:` and comment lines are
ignored. Events are parsed the same whether they arrive one at a time or together.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator


def _feed(line: str, buffer: list[str]) -> str | None:
    """Process one line. Returns a completed event payload on a blank line."""
    if line == "":
        if buffer:
            data = "\n".join(buffer)
            buffer.clear()
            return data
        return None
    if line.startswith(":"):
        return None
    if line.startswith("data:"):
        buffer.append(line[5:].lstrip(" "))
    return None


def iter_sse(lines: Iterable[str]) -> Iterator[str]:
    buffer: list[str] = []
    for line in lines:
        event = _feed(line, buffer)
        if event is not None:
            yield event
    # Any content left in `buffer` was not terminated by a blank line, meaning
    # the connection was severed mid-event. Drop it rather than emit a truncated
    # (and likely un-parseable) payload as if it were a complete event.


async def aiter_sse(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    buffer: list[str] = []
    async for line in lines:
        event = _feed(line, buffer)
        if event is not None:
            yield event
    # See iter_sse: an un-terminated trailing buffer is a mid-event disconnect;
    # drop it instead of yielding a truncated payload.
