"""Check an image, audio clip or video for provenance and matches to your catalogue.

CANTIVIA_API_KEY=... python examples/media_forensics.py clip.mp4 video video/mp4
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Literal, cast

from cantivia import CantiviaClient


def main(path: str, modality: str, mime: str | None) -> None:
    if modality not in ("image", "audio", "video"):
        sys.exit("modality must be image, audio or video")
    media = base64.b64encode(Path(path).read_bytes()).decode()
    with CantiviaClient() as client:
        result = client.verify_media(media, cast(Literal["image", "audio", "video"], modality), mime=mime)

    # "unknown" means no check found anything, not that the file is authentic.
    print(f"Verdict: {result.verdict}")
    for channel in result.channels:
        print(f"- {channel.channel}: {channel.verdict}. {channel.detail}")
        for caveat in channel.caveats:
            print(f"    {caveat}")
    for missing in result.unavailable:
        print(f"- {missing.channel} could not run: {missing.reason}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("usage: media_forensics.py <file> <image|audio|video> [mime type]")
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
