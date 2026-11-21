"""The `leaks` subcommand and the prek hook beside it: no PUSH_BLOCKLIST
value on any surface a PR publishes (#189).
"""

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


BLOCKLIST_HOOK = "scripts/check-push-blocklist.sh"


def test_prek_carries_gitleaks_and_the_blocklist_hook():
    """Layer 1 of #189: the local gate in every rendered repo, and in
    this repo's own (deliberately divergent) config too — gitleaks for
    credentials, and the identity script at all three stages (#313;
    its behavior lives in tests/test_push_blocklist_hook.py)."""
    template = (
        ROOT
        / "template"
        / "{% if agentic_precommit == 'prek' %}.pre-commit-config.yaml{% endif %}.jinja"
    ).read_text()
    own = (ROOT / ".pre-commit-config.yaml").read_text()
    for config in (template, own):
        assert "https://github.com/gitleaks/gitleaks" in config
        assert "rev: v8.30.1" in config
        for mode in ("--files", "--message", "--push"):
            assert f"entry: {BLOCKLIST_HOOK} {mode}" in config
        _, tail = config.split("id: push-blocklist-msg", 1)
        assert "stages: [commit-msg]" in tail.split("- id:")[0]
        _, tail = config.split("id: push-blocklist-push", 1)
        assert "stages: [pre-push]" in tail.split("- id:")[0]
        assert "PUSH_BLOCKLIST:-" not in config, "no inline hook left"
