"""What `record.py submit` does, once a session is finished.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

Reads the records the session branch added, bumps the counter of every
rule a preference-driven record cited, and opens the PR — or prints the
handoff when nothing in the environment can open one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from record_confirm import bump_preference_counter, confirmations_for  # noqa: E402
from record_store import fail, github_slug, run_git  # noqa: E402


def session_records(repo_dir: Path, base_commit: str) -> list[dict]:
    """Every record this session branch added, read from disk."""
    added = run_git(
        repo_dir,
        "diff",
        "--name-only",
        "--diff-filter=A",
        f"{base_commit}..HEAD",
        "--",
        "decisions/",
    ).split()
    # Scoped to decisions/ deliberately: a prediction is an agent's own
    # choice, and letting one reach the counters would be the rule
    # confirming itself through a second door.
    records = []
    for name in added:
        path = repo_dir / name
        records.append(json.loads(path.read_text(encoding="utf-8")))
    if not records:
        raise fail("no records on this session branch — nothing to submit")
    return records


def _bump_cited_rule(
    repo_dir: Path,
    validator,
    rule: str,
    today: str,
    *,
    independent: bool,
) -> None:
    """Bump one cited rule's counter and commit the render beside it."""
    source_path = repo_dir / validator.PREFERENCES_SOURCE
    rendered_path = repo_dir / validator.PREFERENCES_RENDERED
    if not source_path.exists():
        print(f"WARN: no {validator.PREFERENCES_SOURCE} — cannot bump {rule!r}")
        return
    data, source_errors = validator.parse_preferences(
        source_path.read_text(encoding="utf-8")
    )
    if source_errors:
        raise fail(
            f"{validator.PREFERENCES_SOURCE} is invalid — fix it before "
            "submitting:\n" + "\n".join(source_errors)
        )
    count = bump_preference_counter(
        data, rule, today, validator, independent=independent
    )
    if count is None:
        print(
            f"WARN: cited rule {rule!r} not found in "
            f"{validator.PREFERENCES_SOURCE} — no counter bumped "
            "(proposal?)"
        )
        return
    source_path.write_text(validator.serialize_preferences(data), encoding="utf-8")
    rendered_path.write_text(validator.render_preferences(data), encoding="utf-8")
    run_git(
        repo_dir,
        "add",
        validator.PREFERENCES_SOURCE,
        validator.PREFERENCES_RENDERED,
    )
    run_git(repo_dir, "commit", "-m", f"pref-confirm: {rule} (n={count})")
    print(f"pref-confirm: {rule} (n={count})")


def confirm_preferences(
    repo_dir: Path, records: list[dict], validator, today: str
) -> None:
    """Bump the counter of every rule a preference-driven record cited."""
    for record in records:
        if record.get("prediction_stream") != "preference-driven":
            continue
        confirmations, skipped = confirmations_for(record)
        for rule, reason in skipped:
            print(f"pref-skip: {rule} ({reason}) — no counter bumped")
        for rule, independent in confirmations:
            _bump_cited_rule(repo_dir, validator, rule, today, independent=independent)


def open_pr(branch: str, title: str, body: str) -> None:
    """Open the session's PR, or print the handoff when nothing can."""
    url = os.environ.get("DECISION_MEMORY_URL", "")
    slug = github_slug(url)
    if slug and shutil.which("gh"):
        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                slug,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
            return
        print(
            f"gh pr create failed ({result.stderr.strip()}) — falling back to handoff.",
            file=sys.stderr,
        )

    print()
    print("── PR handoff (managed environment / no usable gh) ──")
    print("The branch is pushed; open the PR with the tooling your")
    print("environment declares, using exactly this title and body:")
    print()
    print(f"Title: {title}")
    print("Body:")
    print(body)
