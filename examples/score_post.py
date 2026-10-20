"""Ask Titrate whether a social post's thread is likely to spread.

CANTIVIA_API_KEY=... python examples/score_post.py https://bsky.app/profile/example.com/post/3kabc
"""

from __future__ import annotations

import sys

from cantivia import CantiviaClient


def main(post_url: str) -> None:
    with CantiviaClient() as client:
        answer = client.score_post(post_url)

    # Titrate scores spread, not truth, and often declines: most posts get no replies.
    if not answer.scored:
        reason = answer.abstain_reason.value if answer.abstain_reason else "unknown"
        print(f"Not scored ({reason}). {answer.detail or ''}")
        return
    outlook = "likely" if answer.is_viral else "not likely"
    print(f"This thread is {outlook} to pass {answer.viral_threshold_size:g} posts.")
    if answer.size_range is not None:
        low, high = answer.size_range.low, answer.size_range.high
        print(f"Threads like it end between {low} and {high} posts ({answer.size_range.level:.0%} of the time).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: score_post.py <post url>")
    main(sys.argv[1])
