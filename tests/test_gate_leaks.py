"""The `leaks` subcommand and the prek hook beside it: no PUSH_BLOCKLIST
value on any surface a PR publishes (#189).
"""

import os
import subprocess

from tests.gate_support import (
    ROOT,
    gate_leaks,
)


def test_parse_blocklist_splits_on_pipes_and_drops_blanks():
    assert gate_leaks.parse_blocklist("alice|Bob Example| |bob@example.org|") == [
        ("alice", "entry 1"),
        ("Bob Example", "entry 2"),
        ("bob@example.org", "entry 4"),
    ]
    assert gate_leaks.parse_blocklist("") == []
    assert gate_leaks.parse_blocklist(None) == []


def test_parse_blocklist_reads_explicit_placeholder_labels():
    """An entry may name its own placeholder (`value=pb:name`), so the
    label survives list edits — a positional 'entry N' drifts the
    moment a term is inserted above it."""
    assert gate_leaks.parse_blocklist("alice=pb:old-account|bob|carol=pb:nickname") == [
        ("alice", "pb:old-account"),
        ("bob", "entry 2"),
        ("carol", "pb:nickname"),
    ]


def test_leak_violations_name_surface_and_entry_never_the_value():
    """The denylist values are themselves the identifying material, and
    a CI log on a public repo is a public surface — so a violation names
    WHERE and WHICH entry, never WHAT (#189)."""
    surfaces = [("PR title", "ship it"), ("commit 3f2a1b0 message", "by alice")]
    values = gate_leaks.parse_blocklist("alice=pb:old-account|bob")
    problems = gate_leaks.leak_violations(surfaces, values)
    assert len(problems) == 1
    assert "commit 3f2a1b0 message" in problems[0]
    assert "pb:old-account" in problems[0]
    assert "alice" not in problems[0]


def test_leak_violations_match_case_insensitively():
    problems = gate_leaks.leak_violations(
        [("PR body", "Mail ALICE@example.org")], gate_leaks.parse_blocklist("alice")
    )
    assert len(problems) == 1
    assert "entry 1" in problems[0]


def test_leak_violations_report_every_hit_pair_once():
    surfaces = [("PR title", "alice and bob"), ("PR body", "bob, bob, bob")]
    problems = gate_leaks.leak_violations(
        surfaces, gate_leaks.parse_blocklist("alice|bob")
    )
    assert len(problems) == 3


def test_empty_blocklist_finds_nothing():
    assert gate_leaks.leak_violations([("PR body", "anything at all")], []) == []


def test_pr_surfaces_include_the_prs_own_comment_threads():
    """Comments publish the instant they are posted, so scanning them
    cannot prevent — but the gate re-runs on exactly these events, and
    a hit blocks merge and goes loud instead of lingering quietly."""
    pr = {"title": "t", "body": "b"}
    comments = [{"id": 9, "user": {"login": "x"}, "body": "general remark"}]
    reviews = [{"id": 4, "user": {"login": "x"}, "body": "review summary"}]
    review_comments = [
        {"id": 7, "user": {"login": "x"}, "body": "inline note", "path": "a.py"}
    ]
    surfaces = dict(
        gate_leaks.pr_surfaces(
            pr,
            [],
            [],
            comments=comments,
            reviews=reviews,
            review_comments=review_comments,
        )
    )
    assert surfaces["comment 9"] == "general remark"
    assert surfaces["review 4"] == "review summary"
    assert surfaces["review comment 7 on a.py"] == "inline note"


def test_referenced_tickets_come_from_any_ref_in_the_body():
    """Every `#n` / `owner/repo#n` the body mentions, keyword or not:
    a see-also leaks exactly like a CLOSES."""
    body = "CLOSES #7, see also pandoscope/skills#9 and #7 again"
    refs = gate_leaks.referenced_tickets(body, "pandoscope/meta")
    assert refs == ["pandoscope/meta#7", "pandoscope/skills#9"]
    assert gate_leaks.referenced_tickets(None, "pandoscope/meta") == []


def test_pr_surfaces_include_branch_name_and_referenced_tickets():
    """The branch name publishes with the PR; referenced tickets are
    already public, so a hit there is red-and-loud at the gate rather
    than prevention — the principal fixes the ticket, the PR unblocks."""
    pr = {"title": "t", "body": "b", "head": {"ref": "claude/7-thing"}}
    tickets = [("o/r#5", "ticket body", [{"id": 3, "body": "a comment"}])]
    surfaces = dict(gate_leaks.pr_surfaces(pr, [], [], tickets=tickets))
    assert surfaces["branch name"] == "claude/7-thing"
    assert surfaces["ticket o/r#5 body"] == "ticket body"
    assert surfaces["ticket o/r#5 comment 3"] == "a comment"


def test_diff_surface_is_the_added_side_only():
    """A removal cannot publish anything main does not already publish,
    and the PR that scrubs a value from main is exactly the one whose
    removal lines carry it — so scanning them made a scrub unable to
    pass (#238). Only `+` lines are the diff's published surface; the
    `+++` header is a filename, not content."""
    patch = (
        "@@ -1,3 +1,3 @@\n"
        " context alice-free\n"
        "-fixture = 'alice@example.org'\n"
        "+fixture = 'nobody@example.org'\n"
    )
    files = [{"filename": "tests/test_x.py", "patch": patch}]
    surfaces = dict(gate_leaks.pr_surfaces({"title": "t", "body": "b"}, [], files))
    surface = surfaces["diff of tests/test_x.py"]
    assert "nobody@example.org" in surface
    assert "alice" not in surface
    assert "+++" not in surface
    assert (
        gate_leaks.leak_violations(
            [("diff of tests/test_x.py", surface)], gate_leaks.parse_blocklist("alice")
        )
        == []
    )


def test_diff_surface_survives_a_missing_patch():
    """Binary and oversized files arrive without a patch; that is no
    surface, not a crash."""
    surfaces = dict(gate_leaks.pr_surfaces({}, [], [{"filename": "img.png"}]))
    assert surfaces["diff of img.png"] == ""


def test_leaks_job_rides_the_gate_everywhere():
    """Layer 2 of #189: the leaks job rides ci-ok in the template AND
    both store variants, so the one required context enforces it. The
    gitleaks binary is pinned by version and checksum — a moving 'latest'
    would let a compromised release into every repo's CI at once."""
    paths = [
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "ci-ok.yml.jinja",
        ROOT / "decision-memory" / ".github" / "workflows" / "ci-ok.yml.jinja",
        ROOT / "evidence-memory" / ".github" / "workflows" / "ci-ok.yml.jinja",
    ]
    for path in paths:
        text = path.read_text()
        assert "check_gate.py leaks" in text, path
        assert "secrets.PUSH_BLOCKLIST" in text, path
        assert "gitleaks_8.30.1_linux_x64.tar.gz" in text, path
        assert (
            "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in text
        ), path


BLOCKLIST_HOOK_ENTRY = (
    'bash -c \'p=$(printf %s "${PUSH_BLOCKLIST:-}" | tr -d "\\r\\n" | '
    'sed "s/=[^|]*//g;s/||*/|/g;s/^|//;s/|$//"); '
    '[ -z "$p" ] || ! grep -IilE -- "$p" "$@"\' --'
)


def test_prek_carries_gitleaks_and_the_blocklist_hook():
    """Layer 1 of #189: the local gate in every rendered repo, and in
    this repo's own (deliberately divergent) config too."""
    template = (
        ROOT
        / "template"
        / "{% if agentic_precommit == 'prek' %}.pre-commit-config.yaml{% endif %}.jinja"
    ).read_text()
    own = (ROOT / ".pre-commit-config.yaml").read_text()
    for config in (template, own):
        assert "https://github.com/gitleaks/gitleaks" in config
        assert "rev: v8.30.1" in config
        assert BLOCKLIST_HOOK_ENTRY in config


def run_blocklist_hook(tmp_path, content, blocklist):
    """The push-blocklist hook's script, invoked as prek invokes it."""
    script = BLOCKLIST_HOOK_ENTRY[len("bash -c '") : -len("' --")]
    target = tmp_path / "staged.md"
    target.write_text(content)
    return subprocess.run(
        ["bash", "-c", script, "--", str(target)],
        env={"PATH": os.environ["PATH"], "PUSH_BLOCKLIST": blocklist},
        capture_output=True,
        text=True,
        check=False,
    )


def test_blocklist_hook_catches_a_planted_value_and_passes_clean(tmp_path):
    """#104 acceptance: a planted fixture value is caught; the hook
    prints only the offending FILENAME (grep -l), never the matched
    line — the terminal transcript may itself be shared."""
    hit = run_blocklist_hook(tmp_path, "contact ALICE@example.org", "alice|bob")
    assert hit.returncode == 1
    assert hit.stdout.strip() == str(tmp_path / "staged.md")
    clean = run_blocklist_hook(tmp_path, "nothing to see", "alice|bob")
    assert clean.returncode == 0
    unset = run_blocklist_hook(tmp_path, "alice everywhere", "")
    assert unset.returncode == 0


def test_blocklist_hook_tolerates_editor_added_newlines_and_stray_pipes(tmp_path):
    """The secret material comes from an editor-saved file, and editors
    add a final newline for good reason. A raw newline (or a doubled or
    dangling pipe) would put an EMPTY alternative into the grep pattern,
    which matches every file — so the hook normalizes the pattern
    instead of demanding a newline-free file."""
    for messy in ("alice|bob\n", "alice||bob", "|alice|bob|", "alice|bob\r\n"):
        clean = run_blocklist_hook(tmp_path, "nothing to see", messy)
        assert clean.returncode == 0, f"blocklist {messy!r} false-positived"
        hit = run_blocklist_hook(tmp_path, "contact alice today", messy)
        assert hit.returncode == 1, f"blocklist {messy!r} missed a real hit"
    only_noise = run_blocklist_hook(tmp_path, "anything", "|\n")
    assert only_noise.returncode == 0


def test_blocklist_hook_also_gates_the_commit_message(tmp_path):
    """Commit messages auto-publish on push with no approval step, so
    the local gate covers them too: a second hook with the same entry
    at the commit-msg stage, where git hands it the message file. The
    functional path is the same entry already proven above; this pins
    the wiring in both prek configs."""
    template = (
        ROOT
        / "template"
        / "{% if agentic_precommit == 'prek' %}.pre-commit-config.yaml{% endif %}.jinja"
    ).read_text()
    own = (ROOT / ".pre-commit-config.yaml").read_text()
    for config in (template, own):
        _, tail = config.split("id: push-blocklist-msg", 1)
        assert "stages: [commit-msg]" in tail.split("- id:")[0]
        assert config.count(BLOCKLIST_HOOK_ENTRY) == 2


def test_blocklist_hook_matches_the_value_not_its_placeholder_label(tmp_path):
    """`value=pb:name` entries: the label is metadata, never a pattern —
    a file merely MENTIONING the placeholder must pass, and the value
    still fails."""
    labeled = "alice=pb:old-account|bob"
    hit = run_blocklist_hook(tmp_path, "alice was here", labeled)
    assert hit.returncode == 1
    mention = run_blocklist_hook(tmp_path, "scrubbed to pb:old-account", labeled)
    assert mention.returncode == 0
