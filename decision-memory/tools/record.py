#!/usr/bin/env python3
"""Decision recorder — writer-side tooling for a decision-memory repo.

This docstring doubles as the CLI help text and is the AUTHORITATIVE
description of the recorder's behavior — there is no separate spec
file (design history: agentic-engineering-template issue #37 and the
backfilled decision records). The contract this tool must satisfy
(record schema, commit types, PR flow) lives with the data, in the
decision-memory repo's docs/conventions.md and CI guards.

Verbs:
  open     start a recording session in this store checkout — verify
           it is the store (origin matched against DECISION_MEMORY_URL
           by owner/repo tail, since managed environments rewrite
           remotes), capture the preference-set SHA, create the
           session branch, run the stateless closed-unmerged-PR sweep
  record   mint + validate + write one decision record per input
           draft (stdin JSON object/array, or --from drafts.json),
           one commit per record, each pushed as it lands so the
           clone holds nothing the remote does not; batch-local slug
           references (supersedes_slug, drill_down_of_slug,
           related_slugs) resolve to the minted IDs. `--predict`
           writes to predictions/ instead: an autonomous run's own
           choices under the active preference set, with no decider
           present — replay material, never preference input
  check    validate both record corpora + dangling refs + the
           preference-set pair (schema, mirror, token budget)
  submit   compute two-stream hit rates (refined and near-tie
           bucketed separately), auto-bump pref-confirm counters for
           clean preference-driven hits, push, open the PR (or emit
           the managed-environment handoff)
  propose  write a preference-rule proposal file with its commit

Configuration: DECISION_MEMORY_URL (full git URL of the data repo;
never commit it anywhere public).

The recorder ships inside the store and always operates on the
checkout it lives in. Clone the store, then run its copy:

    git clone "$DECISION_MEMORY_URL" <dir>
    python <dir>/tools/record.py open

Clone fresh per session rather than reusing an attached checkout: a
fresh clone is clean and on the default branch, which is what keeps a
session's PR to that session's own records. Where cloning is
impossible, run the copy inside whatever store checkout is available
— same invariant, no special flag.

Stdlib only. The universal half of the contract lives in
``record_core.py``, shared with every other store's writer; what stays
here is this store's own policy and its IO SHELL. Keep the seam
strict.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import record_core  # noqa: E402  (path bootstrap above)

from record_confirm import (  # noqa: E402
    bump_preference_counter,
    build_pr_body,
    confirmations_for,
    session_hit_rates,
)
from record_store import (  # noqa: E402
    STATE_FILE,
    check_store_checkout,
    commit_record,
    covered_closures,
    default_branch,
    fail,
    github_slug,
    list_closed_unmerged_prs,
    load_corpus,
    load_state,
    load_validator,
    push_session,
    read_drafts,
    run_git,
)

# ======================= store record policy ========================
# The universal contract — envelope grammar, ID minting, field
# ordering, serialization, batch-ref resolution — lives in
# record_core.py, one copy shared by every store's writer. It is
# re-exported here so this module's public surface is unchanged.
# DECISION: the validator is imported from the data-repo clone's
# vendored copy (single copier-vendored source shared with CI), so
# writer-side and CI validation cannot drift. That is why validation
# is defined neither here nor in the core.

SCHEMA_VERSION = record_core.SCHEMA_VERSION
MAX_SLUG_LENGTH = record_core.MAX_SLUG_LENGTH
SLUG_RE = record_core.SLUG_RE

mint_id = record_core.mint_id
serialize_record = record_core.serialize_record
resolve_batch_refs = record_core.resolve_batch_refs

# What makes a record from this store a decision rather than any other
# kind sharing the envelope.
RECORD_TYPE = "decision"

# Replay-ready order: envelope, then input side (pre-ruling), then
# output side (post-ruling), then links. Unknown fields keep their
# draft order after these.
FIELD_ORDER = (
    "v",
    "type",
    "id",
    "date",
    "project",
    "question",
    "context",
    "options",
    "prediction_stream",
    "preference_set",
    "artifact_ref",
    "session",
    "chosen_slot",
    "chosen",
    "operative_reason",
    "correction",
    "rejections",
    "outcome",
    "drill_down_of",
    "closure_of",
    "related",
    "supersedes",
    "notes",
)


def mint_envelope(slug: str, now: dt.datetime) -> dict:
    """Mint this store's envelope: v, type, id."""
    return record_core.mint_envelope(slug, now, RECORD_TYPE)


def draft_to_record(
    draft: dict,
    now: dt.datetime,
    session: str | None = None,
    preference_commit: str | None = None,
) -> dict:
    """Turn a draft record (schema minus tool-minted fields, plus
    ``slug``) into a full record.

    Draft-supplied values always win over minted defaults; unknown
    fields are preserved. Raises ValueError when ``slug`` is missing
    or malformed.
    """
    payload = dict(draft)
    slug = payload.pop("slug", None)
    if not isinstance(slug, str) or not slug:
        raise ValueError("draft is missing the writer-chosen 'slug' field")

    merged = mint_envelope(slug, now)
    merged["date"] = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%d")
    merged["session"] = payload.pop("session", None) or session
    preference_set = payload.pop("preference_set", None)
    if preference_set is None and preference_commit:
        preference_set = {"commit": preference_commit}
    if preference_set is not None:
        merged["preference_set"] = preference_set
    merged.update(payload)

    return record_core.order_fields(merged, FIELD_ORDER)


# ============================ IO shell =============================
# CLI, clone/branch/commit mechanics, PR calls. The PR call is the one
# forge-specific piece: a hosting supersession (or a managed
# environment without gh) swaps/skips this function, never the core.


def store_root() -> Path:
    """The store checkout this recorder lives in.

    Returns the repository root (this file is at <root>/tools/record.py).
    Raises SystemExit when that root is not a git checkout.

    DECISION: the recorder always operates on its own checkout. It is
    stamped into stores by the decision-memory subtemplate, so "which checkout?"
    has exactly one answer and needs no flag, env var or search to
    resolve.
    """
    repo_dir = Path(__file__).resolve().parents[1]
    if not (repo_dir / ".git").exists():
        raise fail(
            f"{repo_dir} is not a git checkout — run the copy of record.py "
            "inside a decision-memory store clone, not a loose copy of the "
            "file (clone the store first: git clone $DECISION_MEMORY_URL)"
        )
    return repo_dir


def cmd_open(args: argparse.Namespace) -> int:
    url = os.environ.get("DECISION_MEMORY_URL")
    if not url:
        raise fail(
            "DECISION_MEMORY_URL is unset — export the full git URL of "
            "your decision-memory repo (never commit it anywhere public)"
        )
    now = dt.datetime.now(dt.timezone.utc)
    repo_dir = store_root()
    check_store_checkout(repo_dir, url)

    # DECISION: the session branch is based on origin/<default>, never
    # on HEAD. A checkout parked on a previous session's unmerged
    # branch is the normal state of any reused checkout, and branching
    # from it silently folds that session's records into this one's PR
    # (#64).
    base = default_branch(repo_dir)
    run_git(repo_dir, "fetch", "--quiet", "origin", base)
    base_ref = f"origin/{base}"
    base_commit = run_git(repo_dir, "rev-parse", base_ref).strip()
    branch = "session/" + now.strftime("%Y%m%dT%H%M%SZ")
    run_git(repo_dir, "checkout", "--quiet", "-b", branch, base_ref)

    session = args.session or os.environ.get("CLAUDE_SESSION_ID")
    # DECISION: session state lives inside the ephemeral clone
    # (untracked file, excluded from git status) — nothing persists
    # outside the temp dir, keeping sessions stateless across machines.
    state = {
        "branch": branch,
        "base_commit": base_commit,
        "session": session,
        "opened_at": now.isoformat(),
    }
    (repo_dir / STATE_FILE).write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )
    exclude = repo_dir / ".git" / "info" / "exclude"
    with open(exclude, "a", encoding="utf-8") as handle:
        handle.write(f"{STATE_FILE}\n")

    records = load_corpus(repo_dir)
    covered = covered_closures(records)
    closed_prs = list_closed_unmerged_prs(url)

    print(f"Store checkout: {repo_dir}")
    print(f"Session branch: {branch}")
    print(f"preference_set.commit for this session: {base_commit}")
    print()
    if closed_prs is None:
        print(
            "Unmerged-PR sweep: could not list closed PRs here (no usable "
            "gh). Handoff: list this repo's closed-UNMERGED PRs with your "
            "environment's tooling and record one decision per PR number "
            f"not in the covered set {sorted(covered)} (set closure_of)."
        )
    else:
        pending = sorted(set(closed_prs) - covered)
        if pending:
            print(
                f"Unmerged-PR sweep: PR(s) {pending} were closed without "
                "merge and have no closure record yet. Record one decision "
                "each ('why was PR #N rejected'), with closure_of set and "
                "the correction flag where applicable."
            )
        else:
            print("Unmerged-PR sweep: all closures covered.")
    print()
    print(
        "Reminder: inject preferences.txt (and ONLY preferences.txt) into "
        "the session context now, if not already injected."
    )
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Write decision records, or predictions with ``--predict``.

    Predictions land in a separate corpus with the same schema and the
    same append-only guarantee. They are what an autonomous run chose
    under the active preference set with no decider present — replay
    material, never preference input, so nothing downstream may read
    them as rulings.
    """
    repo_dir = store_root()
    state = load_state(repo_dir)
    validator = load_validator(repo_dir)
    now = dt.datetime.now(dt.timezone.utc)

    drafts = read_drafts(args)
    try:
        drafts = resolve_batch_refs(drafts, now)
    except ValueError as exc:
        raise fail(str(exc))
    records = []
    errors: list[str] = []
    for index, draft in enumerate(drafts):
        try:
            record = draft_to_record(
                draft,
                now,
                session=state.get("session"),
                preference_commit=state.get("base_commit"),
            )
        except ValueError as exc:
            errors.append(f"draft[{index}]: {exc}")
            continue
        for error in validator.validate_record(record, filename_stem=record["id"]):
            errors.append(f"draft[{index}] ({record['id']}): {error}")
        records.append(record)

    if errors:
        for error in errors:
            print(f"INVALID: {error}", file=sys.stderr)
        print(
            f"{len(errors)} validation error(s) — nothing written.",
            file=sys.stderr,
        )
        return 1

    directory = "predictions" if getattr(args, "predict", False) else "decisions"
    for record in records:
        commit_record(repo_dir, record, directory)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    repo_dir = store_root()
    validator = load_validator(repo_dir)

    errors: list[str] = []
    records: dict[str, dict] = {}
    # Both corpora share the ID namespace and the link graph, so they
    # validate together: a prediction may reference a decision, and a
    # dangling link is dangling either way.
    for directory in ("decisions", "predictions"):
        corpus = repo_dir / directory
        if not corpus.is_dir():
            continue
        for path in sorted(corpus.iterdir()):
            if path.name.startswith("."):
                continue
            if path.suffix != ".json":
                errors.append(f"{path.name}: non-JSON file in {path.parent.name}/")
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{path.name}: invalid JSON: {exc}")
                continue
            errors.extend(
                f"{path.name}: {error}"
                for error in validator.validate_record(record, filename_stem=path.stem)
            )
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                records[record["id"]] = record
    errors.extend(validator.validate_corpus(records))

    source = repo_dir / validator.PREFERENCES_SOURCE
    if not source.exists():
        # Same reason the CI guard fails here: a store without its
        # active set must say so, not pass by having nothing to check.
        errors.append(
            f"{validator.PREFERENCES_SOURCE}: missing — every store carries "
            f"an active set, empty or not, rendered to "
            f"{validator.PREFERENCES_RENDERED}"
        )
    else:
        data, source_errors = validator.parse_preferences(
            source.read_text(encoding="utf-8")
        )
        errors.extend(f"{validator.PREFERENCES_SOURCE}: {e}" for e in source_errors)
        if not source_errors:
            rendered_path = repo_dir / validator.PREFERENCES_RENDERED
            rendered = (
                rendered_path.read_text(encoding="utf-8")
                if rendered_path.exists()
                else ""
            )
            if validator.render_preferences(data) != rendered:
                errors.append(
                    f"{validator.PREFERENCES_RENDERED}: not the render of "
                    f"{validator.PREFERENCES_SOURCE} — run "
                    "`python .github/store/render_preferences.py render`"
                )
            errors.extend(validator.check_preferences_budget(rendered))

    for error in errors:
        print(f"CHECK FAIL: {error}", file=sys.stderr)
    if not errors:
        print(f"check: {len(records)} record(s) valid, budget OK.")
    return 1 if errors else 0


def cmd_submit(args: argparse.Namespace) -> int:  # noqa: PLR0915 — refactor: #243
    repo_dir = store_root()
    state = load_state(repo_dir)
    validator = load_validator(repo_dir)
    branch = state["branch"]
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    added = run_git(
        repo_dir,
        "diff",
        "--name-only",
        "--diff-filter=A",
        f"{state['base_commit']}..HEAD",
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

    streams = session_hit_rates(records)

    source_path = repo_dir / validator.PREFERENCES_SOURCE
    rendered_path = repo_dir / validator.PREFERENCES_RENDERED
    for record in records:
        if record.get("prediction_stream") != "preference-driven":
            continue
        confirmations, skipped = confirmations_for(record)
        for rule, reason in skipped:
            print(f"pref-skip: {rule} ({reason}) — no counter bumped")
        for rule, independent in confirmations:
            if not source_path.exists():
                print(f"WARN: no {validator.PREFERENCES_SOURCE} — cannot bump {rule!r}")
                continue
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
                continue
            source_path.write_text(
                validator.serialize_preferences(data), encoding="utf-8"
            )
            rendered_path.write_text(
                validator.render_preferences(data), encoding="utf-8"
            )
            run_git(
                repo_dir,
                "add",
                validator.PREFERENCES_SOURCE,
                validator.PREFERENCES_RENDERED,
            )
            run_git(repo_dir, "commit", "-m", f"pref-confirm: {rule} (n={count})")
            print(f"pref-confirm: {rule} (n={count})")

    # Records were pushed as they landed; this catches the counter
    # bumps above and is a no-op when there were none.
    push_session(repo_dir)
    print(f"Pushed {branch}.")

    title = f"decision session {branch.split('/', 1)[1]} — " + (
        f"{len(records)} record(s)"
    )
    body = build_pr_body(records, streams)

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
            return 0
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
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    repo_dir = store_root()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    rule = " ".join(args.rule.split())
    slug = args.slug or re.sub(
        r"-+", "-", re.sub(r"[^a-z0-9]+", "-", rule.lower())
    ).strip("-")[:MAX_SLUG_LENGTH].rstrip("-")
    if not SLUG_RE.match(slug):
        raise fail(f"cannot derive a kebab-case slug from {rule!r}")

    path = repo_dir / "proposals" / f"{today}-{slug}.md"
    if path.exists():
        raise fail(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Preference proposal: {slug}\n\n"
        f"- {rule} [confirmed: 0, last: {today}]\n\n"
        "Promotion is human-only: a `pref-promote` commit moves the rule "
        "into the active set (preferences.json + its render); merging "
        "this file is not promotion.\n",
        encoding="utf-8",
    )
    run_git(repo_dir, "add", str(path))
    run_git(repo_dir, "commit", "-m", f"pref-proposal: {rule}")
    print(f"Proposed: {path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="record.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_open = sub.add_parser("open", help="start a recording session")
    p_open.add_argument(
        "--session",
        help="opaque session grouping key (default: $CLAUDE_SESSION_ID)",
    )
    p_open.set_defaults(func=cmd_open)

    p_record = sub.add_parser("record", help="record decision drafts")
    p_record.add_argument(
        "--from",
        dest="from_file",
        help="JSON file with a draft record or an array of drafts "
        "(default: read stdin)",
    )
    p_record.add_argument(
        "--predict",
        action="store_true",
        help="write to predictions/ instead of decisions/: an autonomous "
        "run's own choices, replay material only, never preference input",
    )
    p_record.set_defaults(func=cmd_record)

    p_check = sub.add_parser("check", help="validate the whole corpus")
    p_check.set_defaults(func=cmd_check)

    p_submit = sub.add_parser("submit", help="push and open the session PR")
    p_submit.set_defaults(func=cmd_submit)

    p_propose = sub.add_parser("propose", help="propose a preference rule")
    p_propose.add_argument("--rule", required=True, help="the rule text")
    p_propose.add_argument("--slug", help="override the derived file slug")
    p_propose.set_defaults(func=cmd_propose)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
