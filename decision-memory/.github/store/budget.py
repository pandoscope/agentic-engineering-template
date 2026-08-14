"""Token-budget status for the rendered preference set.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

`preferences.tsv` — the render of `preferences.json` — is injected
into every grilled session, so its size is a permanent per-session
context tax. This module is the reporting
layer on top of the vendored budget check: it reports a level
(`ok` / `warn` / `over`) against the repo-local budget so the push-to-
main workflow can open or update the pinned "compression due" issue and
the PR guard can fail work that pushes the file past 100%.

Token counting itself is NOT reimplemented here: `estimate_tokens` from
the vendored validator is the single authority, so the store layer and
the vendored guard can never disagree about how big the file is.

Stdlib only. Usage:

    python .github/store/budget.py --format json
    python .github/store/budget.py --format github   # $GITHUB_OUTPUT
    python .github/store/budget.py --issue-body
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"),
)

import config as store_config  # noqa: E402  (path bootstrap above)
import decision_validator  # noqa: E402  (path bootstrap above)

PREFERENCES_FILENAME = "preferences.tsv"

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_OVER = "over"


def budget_status(text: str, config: dict) -> dict:
    """Return the budget status of the rendered preference set.

    `percent` is of the repo-local budget, so `over` means literally
    >100% — the threshold the issue's CI gate is written against.
    """
    tokens = decision_validator.estimate_tokens(text)
    budget = int(config["budget_tokens"])
    percent = round(tokens * 100 / budget, 1)
    if tokens > budget:
        level = LEVEL_OVER
    elif percent >= config["warn_at_percent"]:
        level = LEVEL_WARN
    else:
        level = LEVEL_OK
    return {
        "tokens": tokens,
        "budget_tokens": budget,
        "percent": percent,
        "warn_at_percent": config["warn_at_percent"],
        "level": level,
    }


def read_preferences(root: str = ".") -> str:
    path = os.path.join(root, PREFERENCES_FILENAME)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def status_line(status: dict) -> str:
    """One-line human summary, used in guard output and issue bodies."""
    return (
        f"{PREFERENCES_FILENAME}: ~{status['tokens']} tokens "
        f"({status['percent']}% of the {status['budget_tokens']}-token budget, "
        f"warn at {status['warn_at_percent']}%)"
    )


def issue_body(status: dict, sha: str | None = None) -> str:
    """Body of the pinned 'compression due' issue.

    Rewritten in place on every push while the file stays at or over the
    warn threshold, so the issue always shows current numbers rather
    than a stale snapshot.
    """
    headline = (
        "`preferences.tsv` is **over budget** — PRs touching the set now fail CI."
        if status["level"] == LEVEL_OVER
        else "`preferences.tsv` is approaching its budget."
    )
    lines = [
        "<!-- managed by .github/workflows/preferences-budget.yml -->",
        "",
        headline,
        "",
        f"- ~{status['tokens']} tokens (estimated)",
        f"- budget: {status['budget_tokens']} tokens "
        f"(`budget_tokens` in `store.config.json`)",
        f"- usage: **{status['percent']}%** (warn at {status['warn_at_percent']}%)",
    ]
    if sha:
        lines.append(f"- measured at: {sha}")
    lines += [
        "",
        "Every rule in this file costs context on every grilled session, forever.",
        "Run the compaction skill from the repo root to shrink it:",
        "",
        "```text",
        "/compact-preferences",
        "```",
        "",
        "The skill merges overlapping rules, drops superseded ones and "
        "tightens wording",
        "while preserving each rule's conditional, falsifiable form, then "
        "replays the last",
        "decisions against the compacted set and gates on the "
        "preference-driven hit rate.",
        "See `.github/store/README.md` for the flow and "
        "`.claude/skills/compact-preferences/SKILL.md` for the procedure.",
        "",
        "This issue is maintained automatically: it is updated on every push to `main`",
        "and closed once the file is back under the warn threshold.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    parser.add_argument(
        "--format",
        choices=("text", "json", "github"),
        default="text",
        help="text summary, JSON status, or key=value pairs for $GITHUB_OUTPUT",
    )
    parser.add_argument(
        "--issue-body",
        action="store_true",
        help="print the 'compression due' issue body instead of the status",
    )
    parser.add_argument("--sha", help="commit SHA to cite in the issue body")
    args = parser.parse_args(argv)

    try:
        config = store_config.load_config(args.root)
    except store_config.ConfigError as exc:
        print(f"CONFIG FAIL: {exc}", file=sys.stderr)
        return 2

    status = budget_status(read_preferences(args.root), config)

    if args.issue_body:
        print(issue_body(status, args.sha))
        return 0
    if args.format == "json":
        print(json.dumps(status, indent=2, sort_keys=True))
    elif args.format == "github":
        output = os.environ.get("GITHUB_OUTPUT")
        rendered = "".join(f"{key}={value}\n" for key, value in sorted(status.items()))
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(rendered)
        print(rendered, end="")
    else:
        print(status_line(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
