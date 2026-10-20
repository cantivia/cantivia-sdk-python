# Fact-checking is coming soon: the API refuses this script until it launches.
"""Fact-check one claim with the async client, printing progress as it streams.

CANTIVIA_API_KEY=... python examples/stream_claim.py "Inflation in Ireland fell last month."
"""

from __future__ import annotations

import asyncio
import sys

from cantivia import (
    VERDICT_LABELS,
    AsyncCantiviaClient,
    DoneChunk,
    ErrorChunk,
    FactcheckThinkingChunk,
    SearchCompleteChunk,
)


async def main(claim: str) -> None:
    async with AsyncCantiviaClient() as client:
        async for chunk in client.verify_stream(claim):
            if isinstance(chunk, SearchCompleteChunk):
                print(f"[found {chunk.result_count} sources]")
            elif isinstance(chunk, FactcheckThinkingChunk):
                print(chunk.delta, end="", flush=True)
            elif isinstance(chunk, DoneChunk):
                print(f"\n\nVerdict: {VERDICT_LABELS.get(chunk.result.verdict, chunk.result.verdict)}")
                print(chunk.result.reasoning)
            elif isinstance(chunk, ErrorChunk):
                print(f"\nThe check failed: {chunk.message}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Inflation in Ireland fell last month."))
