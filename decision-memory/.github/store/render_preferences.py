#!/usr/bin/env python3
"""Render and check the preference-set pair.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

`preferences.json` is the machine-owned source of truth;
`preferences.tsv` is its render and the ONLY file injected into
sessions. The pair is a declared mirror: this tool is the update
mechanism, and the guards fail on any drift.

Verbs:
  render    write preferences.tsv from preferences.json
  check     exit 1 when preferences.tsv is not the current render

Stdlib only. Usage:

    python .github/store/render_preferences.py render
    python .github/store/render_preferences.py check
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"),
)

import decision_validator  # noqa: E402  (path bootstrap above)


def _paths(root: str) -> tuple[str, str]:
    return (
        os.path.join(root, decision_validator.PREFERENCES_SOURCE),
        os.path.join(root, decision_validator.PREFERENCES_RENDERED),
    )


def _load_source(source_path: str) -> tuple[dict | None, list[str]]:
    if not os.path.isfile(source_path):
        return None, [f"{source_path} is missing"]
    with open(source_path, encoding="utf-8") as handle:
        data, errors = decision_validator.parse_preferences(handle.read())
    if errors:
        return None, [f"{decision_validator.PREFERENCES_SOURCE}: {e}" for e in errors]
    return data, []


def cmd_render(root: str) -> int:
    source_path, rendered_path = _paths(root)
    data, errors = _load_source(source_path)
    if errors:
        for error in errors:
            print(f"RENDER FAIL: {error}", file=sys.stderr)
        return 1
    rendered = decision_validator.render_preferences(data)
    with open(rendered_path, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    tokens = decision_validator.estimate_tokens(rendered)
    print(
        f"rendered {len(data['rules'])} rule(s) -> "
        f"{decision_validator.PREFERENCES_RENDERED} (~{tokens} tokens)"
    )
    return 0


def cmd_check(root: str) -> int:
    source_path, rendered_path = _paths(root)
    data, errors = _load_source(source_path)
    if errors:
        for error in errors:
            print(f"CHECK FAIL: {error}", file=sys.stderr)
        return 1
    given = ""
    if os.path.isfile(rendered_path):
        with open(rendered_path, encoding="utf-8") as handle:
            given = handle.read()
    if decision_validator.render_preferences(data) != given:
        print(
            f"CHECK FAIL: {decision_validator.PREFERENCES_RENDERED} is not "
            f"the render of {decision_validator.PREFERENCES_SOURCE} — run "
            "`python .github/store/render_preferences.py render`",
            file=sys.stderr,
        )
        return 1
    print("mirror is current.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=("render", "check"))
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args(argv)
    return {"render": cmd_render, "check": cmd_check}[args.verb](args.root)


if __name__ == "__main__":
    sys.exit(main())
