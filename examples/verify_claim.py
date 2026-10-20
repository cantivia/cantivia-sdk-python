# Fact-checking is coming soon: the API refuses this script until it launches.
"""Fact-check one claim.

CANTIVIA_API_KEY=... python examples/verify_claim.py "The Eiffel Tower is in Berlin."
"""

from __future__ import annotations

import sys

from cantivia import VERDICT_LABELS, CantiviaClient


def main() -> None:
    claim = sys.argv[1] if len(sys.argv) > 1 else "The Eiffel Tower is in Berlin."
    with CantiviaClient() as client:
        result = client.verify(claim)

    if result.degraded:
        print("The check could not run, so there is no verdict. Try again later.")
        return
    print(f"Verdict: {VERDICT_LABELS.get(result.verdict, result.verdict)}")
    print(result.reasoning)
    for citation in result.citations:
        print(f"- {citation.url}\n  {citation.quote}")


if __name__ == "__main__":
    main()
