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
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import record_core  # noqa: E402  (path bootstrap above)

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

STATE_FILE = ".recorder-session.json"
VALIDATOR_RELPATH = Path(".github") / "guards" / "decision_validator.py"
GITHUB_URL_RE = re.compile(
    r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def repo_url_tail(url: str) -> str:
    """Normalize a git URL to its trailing owner/repo pair (lowercase).

    Managed environments rewrite remotes through local proxies, so two
    URLs for the same repo rarely match textually — the owner/repo
    tail is the stable identity across https/ssh/proxy forms.
    """
    path = url.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [p for p in path.replace(":", "/").split("/") if p]
    return "/".join(parts[-2:]).lower()


def fail(message: str) -> "SystemExit":
    return SystemExit(f"record.py: error: {message}")


def run_git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise fail(f"git {' '.join(args)} failed in {repo_dir}:\n{result.stderr}")
    return result.stdout


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


def load_state(repo_dir: Path) -> dict:
    state_path = repo_dir / STATE_FILE
    if not state_path.exists():
        raise fail(
            f"{state_path} missing — this clone was not created by `record.py open`"
        )
    return json.loads(state_path.read_text(encoding="utf-8"))


def load_validator(repo_dir: Path):
    """Import the copier-vendored validator from the data-repo clone."""
    path = repo_dir / VALIDATOR_RELPATH
    if not path.exists():
        raise fail(
            f"vendored validator missing at {path} — the data repo must "
            "vendor the decision-memory subtemplate (copier update from the "
            "agentic-engineering-template decision-memory subtemplate)"
        )
    spec = importlib.util.spec_from_file_location("decision_validator", path)
    if spec is None or spec.loader is None:
        raise fail(f"cannot import validator from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_corpus(repo_dir: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    decisions_dir = repo_dir / "decisions"
    if decisions_dir.is_dir():
        for path in sorted(decisions_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                records[record["id"]] = record
    return records


def read_drafts(args: argparse.Namespace) -> list[dict]:
    if getattr(args, "from_file", None):
        text = Path(args.from_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise fail(f"input is not valid JSON: {exc}")
    drafts = data if isinstance(data, list) else [data]
    if not all(isinstance(d, dict) for d in drafts):
        raise fail("input must be a JSON object or an array of objects")
    return drafts


def github_slug(url: str) -> str | None:
    match = GITHUB_URL_RE.search(url)
    return f"{match['owner']}/{match['repo']}" if match else None


def list_closed_unmerged_prs(url: str) -> list[int] | None:
    """Best-effort PR listing via gh. None = unavailable (handoff)."""
    slug = github_slug(url)
    if slug is None or shutil.which("gh") is None:
        return None
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            slug,
            "--state",
            "closed",
            "--json",
            "number,mergedAt",
            "--limit",
            "500",
        ],
        capture_output=True,
        text=True,
    )
    # DECISION: any gh failure falls through to the handoff path —
    # managed environments sabotage gh, so failure is an expected mode,
    # not an error.
    if result.returncode != 0:
        return None
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return [
        pr["number"] for pr in prs if isinstance(pr, dict) and not pr.get("mergedAt")
    ]


def covered_closures(records: dict[str, dict]) -> set[int]:
    return {
        record["closure_of"]
        for record in records.values()
        if isinstance(record.get("closure_of"), int)
    }


def check_store_checkout(repo_dir: Path, url: str) -> None:
    """Confirm this checkout is the store, and is safe to record into.

    Raises SystemExit when origin does not match DECISION_MEMORY_URL or
    the worktree is dirty.
    """
    origin = run_git(repo_dir, "config", "--get", "remote.origin.url").strip()
    # DECISION: matched by owner/repo tail, not textually — managed
    # environments rewrite remotes through a local proxy, so a
    # proxy-rewritten origin never equals the configured URL.
    if repo_url_tail(origin) != repo_url_tail(url):
        raise fail(
            f"{repo_dir}: origin {origin!r} is not the store repo "
            f"(DECISION_MEMORY_URL points at {repo_url_tail(url)!r})"
        )
    if run_git(repo_dir, "status", "--porcelain").strip():
        raise fail(
            f"{repo_dir}: worktree is dirty — commit or stash before "
            "opening a recording session in it"
        )


def default_branch(repo_dir: Path) -> str:
    """The store's default branch, as origin advertises it.

    Returns the short name (e.g. "main"). Asks the remote once when the
    clone has no origin/HEAD recorded. Raises SystemExit when origin
    advertises no default branch at all.
    """
    for refresh in (False, True):
        if refresh:
            # Harmless when origin/HEAD is already set; needed for
            # clones made before the remote had any branches.
            subprocess.run(
                ["git", "-C", str(repo_dir), "remote", "set-head", "origin", "--auto"],
                capture_output=True,
                text=True,
            )
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "symbolic-ref",
                "--short",
                "refs/remotes/origin/HEAD",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("/", 1)[1]
    raise fail(
        f"{repo_dir}: origin advertises no default branch — cannot pick a "
        "base for the session branch"
    )


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
        "Reminder: inject preferences.tsv (and ONLY preferences.tsv) into "
        "the session context now, if not already injected."
    )
    return 0


def commit_record(repo_dir: Path, record: dict, directory: str = "decisions") -> None:
    record_id = record["id"]
    path = repo_dir / directory / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise fail(f"{path} already exists — records are immutable")
    path.write_text(serialize_record(record), encoding="utf-8")
    slug = record_id.split("Z-", 1)[1]
    chosen = " ".join(str(record.get("chosen", "")).split())
    if len(chosen) > 100:
        chosen = chosen[:99] + "…"
    # Subject grammar authority: the store's docs/conventions.md
    # (§ Commit types); the vendored guard lints what this composes.
    kind = "decision" if directory == "decisions" else "prediction"
    subject = f"{kind}({record['project']}): {slug} — {chosen}"
    run_git(repo_dir, "add", str(path))
    run_git(repo_dir, "commit", "--quiet", "-m", subject)
    push_session(repo_dir)
    print(f"Recorded {record_id} ({subject})")


def push_session(repo_dir: Path) -> None:
    """Publish the session branch as it stands.

    DECISION: every record is pushed the moment it is committed, so the
    clone holds nothing the remote does not. Sessions run in ephemeral
    clones — that is what makes the clone disposable and its location
    irrelevant, instead of a durability question.

    Raises SystemExit when the push fails: a silently unpushed record
    is exactly the loss this is here to prevent.
    """
    branch = run_git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "push", "--quiet", "-u", "origin", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise fail(
            f"pushing {branch} to origin failed:\n{result.stderr}\n"
            "The record is committed locally but not published — retry, or "
            "push manually before this clone goes away."
        )


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
    if (repo_dir / "preferences.md").exists() and not source.exists():
        errors.append(
            "preferences.md: the active set now lives in "
            f"{validator.PREFERENCES_SOURCE} + {validator.PREFERENCES_RENDERED} "
            "— convert the rules and remove this file"
        )
    if source.exists():
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


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def bump_preference_counter(
    data: dict,
    rule: str,
    today: str,
    validator,
    *,
    independent: bool = False,
) -> int | None:
    """Bump the confirmation counter of the rule matching ``rule``,
    in the parsed `preferences.json` data, in place.

    ``validator`` is the data repo's vendored decision_validator — the
    single source of the preference-set schema, shared with the CI
    guard, so writer and guard cannot disagree about the format.

    ``independent`` also raises the `independent` count. It is true only
    when the confirmation was NOT the rule crediting itself: the rule
    was cited on an option, and the decider chose that option, but it
    was not the slot the rule was written into. The two are counted
    apart because `confirmed` alone also rises when a rule predicts the
    slot it authored, which reads as evidence without being any.

    Returns the new count, or None when no rule matches. Matching is by
    normalized containment, so a cited fragment finds its rule.
    """
    wanted = _normalize(rule)
    for entry in data.get("rules", []):
        if wanted not in _normalize(entry["rule"]):
            continue
        entry[validator.COUNTER_KEY] += 1
        entry[validator.DATE_KEY] = today
        if independent:
            entry[validator.INDEPENDENT_KEY] += 1
        return entry[validator.COUNTER_KEY]
    return None


def session_hit_rates(records: list[dict]) -> dict[str, dict[str, int]]:
    streams: dict[str, dict[str, int]] = {
        "preference-driven": {"hit": 0, "miss": 0, "near-tie": 0, "refined": 0},
        "cold": {"hit": 0, "miss": 0, "near-tie": 0, "refined": 0},
    }
    for record in records:
        stream = record.get("prediction_stream")
        outcome = record.get("outcome")
        if stream in streams and outcome in streams[stream]:
            streams[stream][outcome] += 1
    return streams


def prediction_rules(record: dict) -> list[str]:
    for option in record.get("options", []):
        if isinstance(option, dict) and option.get("role") in (
            "prediction",
            "prediction+recommendation",
        ):
            rules = option.get("rules_cited")
            return [r for r in rules if isinstance(r, str)] if rules else []
    return []


def independent_rules(record: dict) -> list[str]:
    """Rules confirmed by the decider WITHOUT the rule picking the slot.

    `prediction_rules` returns the rules cited on the prediction slot,
    and a `hit` means that slot was chosen — so every confirmation it
    yields is the rule agreeing with the option it authored. That is
    worth counting, but it is not evidence the rule tracks the decider.

    This is the other case: a rule cited on some non-prediction option
    that the decider chose anyway. The rule did not put the option in
    front of them, and they took it regardless.
    """
    chosen_slot = record.get("chosen_slot")
    if chosen_slot is None:
        return []
    for option in record.get("options", []):
        if not isinstance(option, dict) or option.get("slot") != chosen_slot:
            continue
        if option.get("role") in ("prediction", "prediction+recommendation"):
            return []
        rules = option.get("rules_cited") or []
        return [rule for rule in rules if isinstance(rule, str)]
    return []


def build_pr_body(records: list[dict], streams: dict[str, dict[str, int]]) -> str:
    def rate(stream: str) -> str:
        counts = streams[stream]
        scored = counts["hit"] + counts["miss"]
        shown = f"{counts['hit']}/{scored} hits" if scored else "no scored"
        extras = [
            f"{counts[bucket]} {bucket}"
            for bucket in ("refined", "near-tie")
            if counts[bucket]
        ]
        if extras:
            shown += f" ({', '.join(extras)})"
        return shown

    lines = [
        f"Decision session PR: {len(records)} record(s).",
        "",
        "Prediction hit rates (two streams):",
        f"- preference-driven: {rate('preference-driven')}",
        f"- cold (control): {rate('cold')}",
    ]
    supersedes = [
        (record["id"], record["supersedes"])
        for record in records
        if record.get("supersedes")
    ]
    if supersedes:
        lines += ["", "Supersedes claims — review explicitly:"]
        lines += [
            f"- {record_id} supersedes {target}" for record_id, target in supersedes
        ]
    closures = [
        (record["id"], record["closure_of"])
        for record in records
        if record.get("closure_of")
    ]
    if closures:
        lines += ["", "Closure records (closed-unmerged PR sweep):"]
        lines += [
            f"- {record_id} explains the closure of PR #{number}"
            for record_id, number in closures
        ]
    return "\n".join(lines) + "\n"


def cmd_submit(args: argparse.Namespace) -> int:
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
        # Two ways a rule earns a confirmation, and they are counted
        # apart. A `hit` credits the rules cited on the slot that won —
        # the rule agreeing with itself. Anything else can still confirm
        # a rule, if the decider chose an option that cited it without
        # that rule having proposed it; that one is independent.
        if record.get("outcome") == "hit":
            confirmations = [(rule, False) for rule in prediction_rules(record)]
        else:
            confirmations = [(rule, True) for rule in independent_rules(record)]
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
