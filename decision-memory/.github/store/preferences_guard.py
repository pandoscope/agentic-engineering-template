"""PR guard for the active preference set.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

Sits on top of the record guard (`.github/guards/guards.py`), which
keeps enforcing append-only `decisions/`, the schema, the
preferences.json/preferences.txt mirror, and the token budget. This
layer adds the PR-level rules the preference-set lifecycle needs.

0. **Extraction pass.** A PR that ADDS decision records must contain a
   `pref-extract:` commit, with no record added after it. The watermark
   for extraction is that commit's position in history, so the check is
   purely positional — nothing to enumerate, nothing to keep in sync
   with the diff, nothing copyable from another branch. A pass that
   found nothing still commits; omitting it is the one thing that
   fails.

Then the three rules the compaction flow needs:

1. **Carve-out label.** Rewriting an EXISTING rule in
   `preferences.json` requires the carve-out label on the PR. Pure
   additions never need it; mechanical `pref-confirm` counter bumps are
   exempt (the vendored guard already validates their counter math).
   `decisions/` gets NO carve-out — append-only there is absolute, and
   this guard never touches that rule.
2. **Replay regression.** A carve-out PR must carry a replay report in
   its description, gated `pass`, and produced against the exact
   `preferences.txt` in the PR head — the report embeds the file's
   sha256, so a stale report from an earlier round fails. A gate of
   `insufficient-evidence` (nothing degraded, too few gated cases to
   say so meaningfully) merges only with the waiver label, so a human
   owns it explicitly. A `fail` gate is never waivable.
3. **Budget.** A PR that touches the preference set fails when the
   rendered file is over 100% of the repo-local budget; at or above the
   warn threshold it prints a warning but passes.

The git-facing parts are thin adapters; the decisions live in pure
functions so they are testable without a fixture repo.

Stdlib only. Usage (see .github/workflows/preferences-guard.yml):

    python .github/store/preferences_guard.py \
        --base <sha> --labels "a,b" --body-file body.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"),
)

import budget as store_budget  # noqa: E402  (path bootstrap above)
import config as store_config  # noqa: E402  (path bootstrap above)
import extraction  # noqa: E402  (path bootstrap above)
import guards  # noqa: E402  (path bootstrap above; vendored, read-only)
import replay  # noqa: E402  (path bootstrap above)

PREFERENCES_FILENAME = store_budget.PREFERENCES_FILENAME

# Re-export: the content address lives in `replay`, which produces the
# reports this module verifies, so the dependency only runs one way.
preferences_sha256 = replay.preferences_sha256

REPLAY_MARKER = "<!-- replay-report -->"
_REPLAY_FENCE_RE = re.compile(
    re.escape(REPLAY_MARKER) + r"\s*```(?:json)?\s*\n(.*?)\n```",
    re.DOTALL,
)


def classify_pref_commits(commits: list[dict]) -> tuple[bool, list[str]]:
    """Decide whether a PR's commits rewrite EXISTING preference rules.

    ``commits`` is a list of ``{"sha", "subject", "old_source",
    "new_source"}`` dicts, oldest first — the two sources being the
    commit's before/after `preferences.json` content (None = absent).
    Returns ``(carve_out_required, notes)``.
    """
    required = False
    notes: list[str] = []
    for commit in commits:
        if commit["old_source"] == commit["new_source"]:
            continue
        short = commit["sha"][:9]
        subject = commit["subject"]
        kind, _ = guards.classify_preferences_change(
            commit["old_source"], commit["new_source"], subject
        )
        if kind in ("none", "addition"):
            continue
        if kind == "bump-exempt":
            notes.append(f"{short}: mechanical pref-confirm counter bump — exempt")
            continue
        if kind == "migration":
            # The one-time doc-field backfill is meaning-preserving — the
            # commit guard already accepts it, and the two must never
            # disagree — so it needs no carve-out label or replay report.
            notes.append(f"{short}: one-time doc-field backfill migration — exempt")
            continue
        required = True
        if kind == "invalid":
            notes.append(
                f"{short}: invalid {guards.PREFERENCES_SOURCE} change "
                f"({subject!r}) — treated as a rewrite"
            )
        else:
            notes.append(f"{short}: rewrites existing preference rules ({subject!r})")
    return required, notes


def extract_replay_report(body: str) -> tuple[dict | None, str | None]:
    """Pull the replay report out of a PR description.

    Returns ``(report, error)`` — exactly one is None.
    """
    match = _REPLAY_FENCE_RE.search(body or "")
    if not match:
        return None, (
            "no replay report in the PR description: add a "
            f"{REPLAY_MARKER} marker followed by a ```json fence holding the "
            "output of `replay.py gate`"
        )
    try:
        report = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return None, f"replay report is not valid JSON: {exc}"
    if not isinstance(report, dict):
        return None, "replay report must be a JSON object"
    return report, None


def check_replay_report(
    body: str, head_preferences: str, waived: bool = False, waiver_label: str = ""
) -> tuple[list[str], list[str]]:
    """Validate the replay report of a carve-out PR.

    Returns ``(errors, notes)``. A ``fail`` gate is never waivable — it
    is a measured regression. An ``insufficient-evidence`` gate is: the
    compaction did not degrade anything, there were simply too few
    preference-driven cases for the pass to carry evidence. Merging
    that is a judgement call, so it needs `waiver_label` on the PR and
    lands in the log as one — which is the whole point of not calling
    it `pass`.
    """
    report, error = extract_replay_report(body)
    if error:
        return [error], []
    assert report is not None
    errors: list[str] = []
    notes: list[str] = []
    gate = report.get("gate")
    if gate == replay.GATE_INSUFFICIENT:
        gated = report.get("gated_cases")
        minimum = report.get("min_gated_cases")
        if waived:
            notes.append(
                f"replay gate is {gate!r} ({gated} of {minimum} "
                f"preference-driven cases) and the {waiver_label!r} label is "
                "present — merging an unvalidated compaction on human judgement"
            )
        else:
            errors.append(
                f"replay gate is {gate!r}: {gated} preference-driven case(s), "
                f"below the {minimum} this store requires. Nothing degraded, "
                "but nothing was validated either — extract rules to grow the "
                f"gated stream, or apply the {waiver_label!r} label to own the "
                "merge explicitly"
            )
    elif gate != replay.GATE_PASS:
        errors.append(
            f"replay report gate is {gate!r}, not 'pass' — the compacted rule "
            "set must not degrade the preference-driven hit rate"
        )
    reported = report.get("candidate_preferences_sha256")
    actual = preferences_sha256(head_preferences)
    if reported != actual:
        errors.append(
            "replay report was produced against a different preferences.txt "
            f"(report {str(reported)[:12]}… vs head {actual[:12]}…) — re-run "
            "the replay after the last edit"
        )
    return errors, notes


def evaluate(
    *,
    commits: list[dict],
    labels: list[str],
    body: str,
    head_preferences: str,
    preferences_touched: bool,
    config: dict,
    extraction_errors: list[str] | None = None,
    extraction_note: str | None = None,
) -> tuple[list[str], list[str]]:
    """Pure core: return ``(errors, notes)`` for one PR."""
    errors: list[str] = []
    carve_out_required, notes = classify_pref_commits(commits)
    label = config["carve_out_label"]

    # A missed pass is recoverable — the watermark walk reaches back
    # past it — but recoverable is not the same as caught: nothing would
    # prompt the recovery. The gate is what turns "can be picked up
    # later" into "was picked up here".
    extraction_errors = list(extraction_errors or [])
    errors.extend(extraction_errors)
    if extraction_note and not extraction_errors:
        notes.append(extraction_note)

    if carve_out_required:
        if label not in labels:
            errors.append(
                f"{guards.PREFERENCES_SOURCE}: existing rules were rewritten "
                f"without the {label!r} label — only a labelled compaction PR "
                "may rewrite the active set (counter bumps via pref-confirm "
                "are exempt)"
            )
        else:
            waiver_label = config["replay_waiver_label"]
            report_errors, report_notes = check_replay_report(
                body,
                head_preferences,
                waived=waiver_label in labels,
                waiver_label=waiver_label,
            )
            errors.extend(report_errors)
            notes.extend(report_notes)
    elif label in labels:
        notes.append(
            f"{label!r} label present but no existing line was edited — nothing to gate"
        )

    status = store_budget.budget_status(head_preferences, config)
    notes.append(store_budget.status_line(status))
    if status["level"] == store_budget.LEVEL_OVER:
        if preferences_touched:
            errors.append(
                f"{PREFERENCES_FILENAME}: {status['percent']}% of the "
                f"{status['budget_tokens']}-token budget — PRs touching the "
                "preference set are blocked until it is compacted back under "
                "budget"
            )
        else:
            notes.append(
                f"{PREFERENCES_FILENAME} is over budget; this PR does not "
                "touch the preference set, so it is not blocked"
            )
    elif status["level"] == store_budget.LEVEL_WARN:
        notes.append(
            "compression due: at or above the warn threshold — run the compaction skill"
        )
    return errors, notes


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


def collect_commits(base: str) -> list[dict]:
    """Every non-merge PR commit with its before/after `preferences.json`."""
    commits: list[dict] = []
    for sha in _git("rev-list", "--no-merges", "--reverse", f"{base}..HEAD").split():
        commits.append(
            {
                "sha": sha,
                "subject": _git("log", "-1", "--format=%s", sha).strip(),
                "old_source": guards.show_file(f"{sha}^:{guards.PREFERENCES_SOURCE}"),
                "new_source": guards.show_file(f"{sha}:{guards.PREFERENCES_SOURCE}"),
            }
        )
    return commits


def _extraction_note(base: str, root: str) -> str | None:
    """Human-facing summary of the pass this PR carries, if any."""
    added = extraction.added_since(base, root, three_dot=True)
    if not added:
        return None
    sha = extraction.last_extraction_commit(f"{base}..HEAD", root)
    if sha is None:
        return None
    return (
        f"extraction pass {sha[:9]} closes this PR's {len(added)} added "
        "record(s); the watermark moves with it"
    )


def preferences_touched(base: str) -> bool:
    changed = [
        name.strip()
        for name in _git("diff", "--name-only", f"{base}...HEAD").split("\n")
    ]
    return guards.PREFERENCES_SOURCE in changed or PREFERENCES_FILENAME in changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base SHA of the PR")
    parser.add_argument("--labels", default="", help="comma-separated PR label names")
    parser.add_argument("--body-file", help="file holding the PR description")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args(argv)

    try:
        config = store_config.load_config(args.root)
    except store_config.ConfigError as exc:
        print(f"GUARD FAIL: {exc}")
        return 1

    body = ""
    if args.body_file and os.path.isfile(args.body_file):
        with open(args.body_file, encoding="utf-8") as handle:
            body = handle.read()

    errors, notes = evaluate(
        commits=collect_commits(args.base),
        labels=[name.strip() for name in args.labels.split(",") if name.strip()],
        body=body,
        head_preferences=store_budget.read_preferences(args.root),
        preferences_touched=preferences_touched(args.base),
        config=config,
        extraction_errors=extraction.check_pass(args.base, args.root),
        extraction_note=_extraction_note(args.base, args.root),
    )
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"GUARD FAIL: {error}")
    if not errors:
        print("Preference guards passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
