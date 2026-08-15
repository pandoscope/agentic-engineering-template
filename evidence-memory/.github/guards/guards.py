"""CI guard for the evidence-memory store.

Copier-vendored from the agentic-engineering-template — do NOT edit in
the store repo; change it in the template and pull via `copier update`.

Protects the store's two invariants: records are append-only, and
every record satisfies the contract. Stdlib only — no dependencies to
install, so the guard keeps working even if the template repo
disappears.

Deliberately smaller than the decision store's guard. That one also
polices its active preference set and per-commit edit rules, because a
decision session is a human-reviewed batch. Evidence records
auto-merge on green, so the guard IS the review: it checks what a
machine can check and claims nothing more.

Usage:

    python .github/guards/guards.py --base «base-sha»
    python .github/guards/guards.py            # corpus checks only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence_validator  # noqa: E402  (path bootstrap above)

RECORDS_DIR = evidence_validator.STORE_DIR


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


def check_append_only(base: str) -> list[str]:
    """No modify/delete/rename ever touches an existing record.

    Moving a file breaks its stable ID and every inbound link, and a
    link cannot be repaired after the fact because the pointing record
    is itself immutable.
    """
    errors: list[str] = []
    diff = _git("diff", "--name-status", "--find-renames", f"{base}...HEAD")
    for line in diff.splitlines():
        parts = line.split("\t")
        status = parts[0]
        paths = parts[1:]
        if any(p.startswith(f"{RECORDS_DIR}/") for p in paths) and status != "A":
            errors.append(
                f"append-only: {RECORDS_DIR}/ change {status} {' '.join(paths)} "
                "— existing records are never modified, deleted, or renamed"
            )
    return errors


def check_corpus(root: str = ".") -> list[str]:
    """Validate every record, then the links between them."""
    errors: list[str] = []
    records: dict[str, dict] = {}
    records_dir = os.path.join(root, RECORDS_DIR)
    if os.path.isdir(records_dir):
        for name in sorted(os.listdir(records_dir)):
            if name.startswith("."):
                continue
            path = os.path.join(records_dir, name)
            if not name.endswith(".json"):
                errors.append(f"{path}: non-JSON file in {RECORDS_DIR}/")
                continue
            stem = name[: -len(".json")]
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{path}: unreadable ({exc})")
                continue
            for error in evidence_validator.validate_record(record, filename_stem=stem):
                errors.append(f"{path}: {error}")
            if isinstance(record, dict):
                records[stem] = record
    errors.extend(evidence_validator.validate_corpus(records))
    errors.extend(evidence_validator.check_tickets_filed(records))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", help="base SHA to diff against for the append-only check"
    )
    parser.add_argument("--root", default=".", help="store root (default: .)")
    args = parser.parse_args(argv)

    errors: list[str] = []
    if args.base:
        errors.extend(check_append_only(args.base))
    errors.extend(check_corpus(args.root))

    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} guard error(s)", file=sys.stderr)
        return 1
    print("guards: ok")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
