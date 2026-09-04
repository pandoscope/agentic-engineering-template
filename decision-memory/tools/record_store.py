"""The recorder's IO shell: the store checkout, git, and the forge.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

CLI-facing mechanics only, kept apart from the verbs in `record.py` and
from the record contract in `record_core.py`. The PR call is the one
forge-specific piece: a hosting supersession (or a managed environment
without gh) swaps or skips it, never the core.
"""

from __future__ import annotations

import argparse
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


def commit_record(repo_dir: Path, record: dict, directory: str = "decisions") -> None:
    record_id = record["id"]
    path = repo_dir / directory / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise fail(f"{path} already exists — records are immutable")
    path.write_text(record_core.serialize_record(record), encoding="utf-8")
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
