# Fact-checking is coming soon: the API refuses this script until it launches.
"""Fact-check every check-worthy claim in a text file.

CANTIVIA_API_KEY=... python examples/verify_article.py article.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

from cantivia import VERDICT_LABELS, CantiviaClient


def main(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    with CantiviaClient() as client:
        result = client.verify_article(text)

    print(f"Overall: {VERDICT_LABELS.get(result.overall_verdict, result.overall_verdict)}. {result.summary}")
    print(f"{result.verified_count} of {result.claim_count} claims settled, {result.degraded_count} not checked.")
    for claim in result.claims:
        print(f"- {VERDICT_LABELS.get(claim.verdict, claim.verdict)}: {claim.claim}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify_article.py <text file>")
    main(sys.argv[1])
