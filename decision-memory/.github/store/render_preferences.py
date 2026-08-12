#!/usr/bin/env python3
"""Render, check, and migrate the preference-set pair.

Copier-vendored from the agentic-engineering-template guard
subtemplate — change it there, pull via `copier update`.

`preferences.json` is the machine-owned source of truth;
`preferences.txt` is its render and the ONLY file injected into
sessions. The pair is a declared mirror: this tool is the update
mechanism, and the guards fail on any drift.

Verbs:
  render    write preferences.txt from preferences.json
  check     exit 1 when preferences.txt is not the current render
  migrate   one-shot conversion of a legacy markdown set into the pair

Stdlib only. Usage:

    python .github/store/render_preferences.py render
    python .github/store/render_preferences.py check
    python .github/store/render_preferences.py migrate
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guards"),
)

import decision_validator  # noqa: E402  (path bootstrap above)

LEGACY_FILENAME = "preferences.md"

# The legacy grammar this tool retires: `- <rule> [k: v, ...]` bullets
# under `## <Section>` headings, wrapped lines rejoined. Kept ONLY for
# `migrate` — everything else reads the JSON.
_LEGACY_SUFFIX_RE = re.compile(r"\[([^\]\[]*)\]\s*$")
_LEGACY_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _kebab(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def _legacy_bullets(text: str) -> list[tuple[str, str]]:
    """(section, joined bullet text) pairs, wrapped lines rejoined."""
    bullets: list[tuple[str, str]] = []
    section = "general"
    current: list[str] | None = None
    for line in text.splitlines():
        heading = _LEGACY_HEADING_RE.match(line)
        if line.startswith("- "):
            if current is not None:
                bullets.append((section, " ".join(current)))
            current = [line[2:].strip()]
            continue
        if current is not None and (not line.strip() or heading):
            bullets.append((section, " ".join(current)))
            current = None
        elif current is not None:
            current.append(line.strip())
        if heading and not line.startswith("- "):
            slug = _kebab(heading.group(1))
            if slug:
                section = slug
    if current is not None:
        bullets.append((section, " ".join(current)))
    return bullets


def parse_legacy(text: str) -> tuple[list[dict], list[str]]:
    """Turn a legacy markdown set into ``rules`` entries.

    Returns ``(rules, errors)``. Strict on the suffix: a bullet without
    the full counter suffix fails the migration rather than minting a
    rule with invented counters.
    """
    rules: list[dict] = []
    errors: list[str] = []
    for section, bullet in _legacy_bullets(text):
        match = _LEGACY_SUFFIX_RE.search(bullet)
        if not match:
            errors.append(f"bullet has no counter suffix: {bullet!r}")
            continue
        pairs: dict[str, str] = {}
        for chunk in match.group(1).split(","):
            key, sep, value = chunk.partition(":")
            if sep:
                pairs[key.strip()] = value.strip()
        rule_text = " ".join(_LEGACY_SUFFIX_RE.sub("", bullet).split())
        try:
            rules.append(
                {
                    "section": section,
                    "rule": rule_text,
                    decision_validator.COUNTER_KEY: int(
                        pairs[decision_validator.COUNTER_KEY]
                    ),
                    decision_validator.INDEPENDENT_KEY: int(
                        pairs.get(decision_validator.INDEPENDENT_KEY, "0")
                    ),
                    decision_validator.DATE_KEY: pairs[decision_validator.DATE_KEY],
                }
            )
        except (KeyError, ValueError) as exc:
            errors.append(f"bullet has a malformed counter suffix ({exc}): {bullet!r}")
    return rules, errors


SOURCE_BANNER = (
    "Machine-owned source of truth for the active preference set — "
    "rendered to preferences.txt, the ONLY file injected into sessions. "
    "Tooling writes both files; never edit either by hand. Seeded once "
    "and owned by the store: rules enter via proposals/ and human "
    "pref-promote commits. See docs/conventions.md."
)


def _paths(root: str) -> tuple[str, str, str]:
    return (
        os.path.join(root, decision_validator.PREFERENCES_SOURCE),
        os.path.join(root, decision_validator.PREFERENCES_RENDERED),
        os.path.join(root, LEGACY_FILENAME),
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
    source_path, rendered_path, _ = _paths(root)
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
    source_path, rendered_path, _ = _paths(root)
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


def cmd_migrate(root: str) -> int:
    source_path, rendered_path, legacy_path = _paths(root)
    if os.path.isfile(source_path):
        print(
            f"MIGRATE FAIL: {decision_validator.PREFERENCES_SOURCE} already "
            f"exists — this store is migrated; remove {LEGACY_FILENAME} by "
            "hand if it is a leftover",
            file=sys.stderr,
        )
        return 1
    if not os.path.isfile(legacy_path):
        print(f"MIGRATE FAIL: {legacy_path} is missing", file=sys.stderr)
        return 1
    with open(legacy_path, encoding="utf-8") as handle:
        rules, errors = parse_legacy(handle.read())
    data = {"_comment": SOURCE_BANNER, "rules": rules}
    errors.extend(decision_validator.validate_preferences(data))
    if errors:
        for error in errors:
            print(f"MIGRATE FAIL: {error}", file=sys.stderr)
        return 1
    with open(source_path, "w", encoding="utf-8") as handle:
        handle.write(decision_validator.serialize_preferences(data))
    rendered = decision_validator.render_preferences(data)
    with open(rendered_path, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    os.remove(legacy_path)
    tokens = decision_validator.estimate_tokens(rendered)
    print(
        f"migrated {len(rules)} rule(s): {LEGACY_FILENAME} -> "
        f"{decision_validator.PREFERENCES_SOURCE} + "
        f"{decision_validator.PREFERENCES_RENDERED} (~{tokens} tokens injected)"
    )
    print(
        'commit both files and the removal together, e.g. as "chore: '
        'migrate the preference set to its split format"'
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=("render", "check", "migrate"))
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args(argv)
    return {"render": cmd_render, "check": cmd_check, "migrate": cmd_migrate}[
        args.verb
    ](args.root)


if __name__ == "__main__":
    sys.exit(main())
