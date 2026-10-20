# Fact-checking is coming soon: the API refuses this script until it launches.
"""Fact-check the text in an image (a screenshot, meme or infographic).

CANTIVIA_API_KEY=... python examples/verify_image.py screenshot.png
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cantivia import VERDICT_LABELS, CantiviaClient


def main(path: str) -> None:
    image = base64.b64encode(Path(path).read_bytes()).decode()
    with CantiviaClient() as client:
        result = client.verify_image(image)

    print(f"Overall: {VERDICT_LABELS.get(result.overall_verdict, result.overall_verdict)}. {result.summary}")
    for claim in result.claims:
        print(f"- {VERDICT_LABELS.get(claim.verdict, claim.verdict)}: {claim.claim}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify_image.py <image file>")
    main(sys.argv[1])
