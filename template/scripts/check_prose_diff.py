#!/usr/bin/env python3
"""Diff-scoped prose checks (#275).

A hunk whose removed and added lines hold the same word sequence
with different line breaks is a reflow.
A reflow outside a `style:` commit pollutes the diff and the blame,
so it is a finding.
A guillemet placeholder in code is a finding;
Markdown keeps them for tracker-bound templates.
Both checks read only the diff, so no file fails on its past.

Modes:
  reflow --msg FILE    commit-msg stage: the staged diff, judged by
                       the subject in FILE
  guillemets FILE...   pre-commit stage: added lines of the staged diff
  --range A..B         CI: each commit in the range, judged by its own
                       subject, for both checks

A commit that changes the copier answers file is a template update.
It carries the upstream's reflows, so the reflow check skips it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field

STYLE = re.compile(r"^style(\([^)]*\))?!?:")
ANSWERS = ".copier-answers.agentic.yml"
# The paths the stamped prek config excludes: vendored or template output.
VENDORED = re.compile(
    r"CHANGELOG\.md|\.copier-answers\.agentic\.yml|\.all-contributorsrc|\.agents/skills|\.claude/skills|scripts/ci/"
)
MARKDOWN = re.compile(r"\.md(\.jinja)?$")
# Leading comment markers, so a rewrapped comment compares by its words.
MARKER = re.compile(r"^\s*(#+|//+|/?\*+/?|--|;+|>)\s?")


@dataclass
class Hunk:
    path: str
    line: int
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def hunks(diff: str) -> list[Hunk]:
    found: list[Hunk] = []
    path = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@") and path:
            start = int(re.search(r"\+(\d+)", line).group(1))
            found.append(Hunk(path, start))
        elif found and found[-1].path == path and path:
            if line.startswith("-") and not line.startswith("---"):
                found[-1].removed.append(line[1:])
            elif line.startswith("+"):
                found[-1].added.append(line[1:])
    return [h for h in found if not VENDORED.search(h.path)]


def words(lines: list[str]) -> list[list[str]]:
    return [MARKER.sub("", line).split() for line in lines]


def is_reflow(hunk: Hunk) -> bool:
    if not hunk.removed or not hunk.added:
        return False
    before, after = words(hunk.removed), words(hunk.added)
    flat = lambda rows: [w for row in rows for w in row]  # noqa: E731
    return flat(before) == flat(after) and before != after


def reflows(diff: str, subject: str, paths: list[str]) -> list[str]:
    if STYLE.match(subject) or ANSWERS in paths:
        return []
    return [
        f"{h.path}:{h.line}: reflow of untouched lines"
        for h in hunks(diff)
        if is_reflow(h)
    ]


def guillemets(diff: str) -> list[str]:
    return [
        f"{h.path}:{h.line}: guillemet in code; use <name> placeholders"
        for h in hunks(diff)
        if not MARKDOWN.search(h.path) and any("«" in a or "»" in a for a in h.added)
    ]


def report(findings: list[str], advice: str) -> int:
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(advice, file=sys.stderr)
    return 1 if findings else 0


REFLOW_ADVICE = (
    "Revert the rewrap, or move it into a style: commit of its own"
    " and list that commit in .git-blame-ignore-revs."
)


def main(argv: list[str]) -> int:
    if argv[:1] == ["reflow"] and argv[1:2] == ["--msg"] and len(argv) == 3:
        with open(argv[2], encoding="utf-8") as handle:
            subject = handle.readline().strip()
        diff = git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff")
        paths = git("diff", "--cached", "--name-only").split()
        return report(reflows(diff, subject, paths), REFLOW_ADVICE)
    if argv[:1] == ["guillemets"]:
        if len(argv) == 1:
            return 0
        diff = git(
            "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--", *argv[1:]
        )
        return report(guillemets(diff), "Guillemets stay in Markdown.")
    if argv[:1] == ["--range"] and len(argv) == 2:
        findings: list[str] = []
        for sha in git("rev-list", "--reverse", "--no-merges", argv[1]).split():
            subject = git("log", "-1", "--format=%s", sha).strip()
            diff = git("show", "-U0", "--no-color", "--no-ext-diff", "--format=", sha)
            paths = git("show", "--name-only", "--format=", sha).split()
            tag = f"{sha[:8]} {subject}: "
            findings += [
                tag + f for f in reflows(diff, subject, paths) + guillemets(diff)
            ]
        return report(findings, REFLOW_ADVICE)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
