#!/usr/bin/env python3
"""Cap on source file length, run over the files a commit touches (#239).

Nothing in the stamped lint config reads file length, so a file grows
until a reader notices. This fails on any source file over the limit,
naming the path and the count, and exempts only what the repo's own
`.file-length-allowlist` names — each entry carrying the ticket that
will bring the file back under, so the list shrinks rather than
becoming permanent.
"""

from __future__ import annotations

import sys


def parse_allowlist(text: str) -> tuple[dict[str, str], list[str]]:
    """`{path: ticket}` from the allowlist, and a problem per bad entry."""
    raise NotImplementedError


def review(
    counts: dict[str, int],
    limit: int,
    allowlist: dict[str, str],
    missing: set[str],
) -> list[str]:
    """Every problem this run found, as lines a reader can act on."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
