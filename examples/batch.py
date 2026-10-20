# Fact-checking is coming soon: the API refuses this script until it launches.
"""Verify a file of claims, one per line, as a background batch.

CANTIVIA_API_KEY=... python examples/batch.py claims.txt
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from cantivia import Batch, CantiviaClient


def show_progress(batch: Batch) -> None:
    print(f"{batch.status}: {batch.done} done, {batch.failed} failed, {batch.pending} pending")


def main(path: str) -> None:
    claims = [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    # The same file always gets the same key, so running this twice does not verify (or bill) it twice.
    key = hashlib.sha256("\n".join(claims).encode()).hexdigest()
    with CantiviaClient() as client:
        accepted = client.submit_batch(claims, idempotency_key=key)
        print(f"Submitted batch {accepted.batch_id} with {accepted.total} claims.")
        batch = client.wait_for_batch(accepted.batch_id, on_progress=show_progress)

    for item in batch.items:
        print(f"{item.idx}: {item.verdict if item.status == 'done' else item.error} - {item.claim}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: batch.py <file with one claim per line>")
    main(sys.argv[1])
