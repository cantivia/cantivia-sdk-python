"""Enrol media you hold the rights to, then list what the catalogue holds.

CANTIVIA_API_KEY=... python examples/catalogue.py press-photo.jpg
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cantivia import CantiviaClient, CatalogueEnrollJob


def show_progress(job: CatalogueEnrollJob) -> None:
    print(f"job {job.job_id}: {job.status} ({job.completed}/{job.total})")


def main(paths: list[str]) -> None:
    assets = [{"name": Path(p).name, "media": base64.b64encode(Path(p).read_bytes()).decode()} for p in paths]
    with CantiviaClient() as client:
        job = client.enroll_media(assets, on_progress=show_progress)
        for asset in job.assets:
            print(f"- {asset.name}: {asset.status} {asset.reason or ''}")

        listing = client.list_catalogue()
        print(f"The catalogue holds {listing.gallery_size} of at most {listing.max_assets} assets.")
        for record in listing.assets:
            print(f"- {record.name} ({record.modality}), enrolled {record.enrolled_at}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: catalogue.py <file> [<file> ...]")
    main(sys.argv[1:])
