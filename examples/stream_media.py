"""Run media forensics on a file with the async client, printing each chunk as it streams.

CANTIVIA_API_KEY=... python examples/stream_media.py clip.mp4 video video/mp4
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from typing import Literal, cast

from cantivia import AsyncCantiviaClient, ErrorChunk, MediaDoneChunk, MediaForensicsChunk


async def main(path: str, modality: str, mime: str | None) -> None:
    if modality not in ("image", "audio", "video"):
        sys.exit("modality must be image, audio or video")
    media = base64.b64encode(Path(path).read_bytes()).decode()
    kind = cast(Literal["image", "audio", "video"], modality)
    async with AsyncCantiviaClient() as client:
        async for chunk in client.verify_media_stream(media, kind, mime=mime):
            if isinstance(chunk, MediaForensicsChunk):
                # "unknown" means no check found anything, not that the file is authentic.
                print(f"Verdict: {chunk.result.verdict}")
                for channel in chunk.result.channels:
                    print(f"- {channel.channel}: {channel.verdict}. {channel.detail}")
            elif isinstance(chunk, MediaDoneChunk):
                print("Done.")
            elif isinstance(chunk, ErrorChunk):
                print(f"The check failed: {chunk.message}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("usage: stream_media.py <file> <image|audio|video> [mime type]")
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None))
