"""Screen a manuscript's title and abstract for paper-mill writing style.

CANTIVIA_API_KEY=... python examples/screen_manuscript.py "Title" abstract.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

from cantivia import CantiviaClient


def main(title: str, abstract_path: str) -> None:
    abstract = Path(abstract_path).read_text(encoding="utf-8")
    with CantiviaClient() as client:
        # budget: the share of submissions your reviewers can read, here one in a hundred.
        screening = client.screen_manuscript(title, abstract, budget=0.01)

    print(f"Decision: {screening.decision}")
    if screening.decision == "review" and screening.precision is not None:
        print(f"Precision measured at this flag rate on held-out papers: {screening.precision:.0%}")
        for term, weight in screening.evidence:
            print(f"- {term} ({weight:+.2f})")
    for caveat in screening.caveats:
        print(f"Note: {caveat}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: screen_manuscript.py <title> <abstract file>")
    main(sys.argv[1], sys.argv[2])
