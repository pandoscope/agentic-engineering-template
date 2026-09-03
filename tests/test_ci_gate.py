"""The uniform CI gate's decision logic (#137, #143).

The scripts' pure functions are imported straight from the template
copy; API access is a thin layer these tests never touch. The copies
in the repo root and the store subtemplates are pinned byte-identical,
template-first, like every other multi-copy file here.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_COPY = ROOT / "template" / "scripts" / "ci" / "check_gate.py"

spec = importlib.util.spec_from_file_location("check_gate", TEMPLATE_COPY)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

KEYWORDS = json.loads(
    (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "reference-keywords.json"
    ).read_text()
)


# ------------------------------------------------------------- ticket


def ticket(body, branch="claude/7-thing", labels=(), author="pando-ramet"):
    return gate.ticket_violations(body, branch, list(labels), author, KEYWORDS)


def test_canonical_caps_reference_passes():
    problems, escaped = ticket("CLOSES #7.\n\nDetails follow.")
    assert problems == [] and not escaped


def test_cross_repo_and_advances_pass():
    problems, _ = ticket("FIXES pandoscope/skills#7 and ADVANCES #7")
    assert problems == []


def test_lowercase_native_keyword_fails_even_beside_a_canonical_one():
    problems, _ = ticket("CLOSES #7, and this also closes #9")
    assert any("closes #9" in p for p in problems)


def test_unlisted_native_keyword_fails_in_any_case():
    problems, _ = ticket("CLOSES #7. Resolves #9 too.")
    assert any("Resolves #9" in p for p in problems)


def test_wrong_case_of_our_own_keyword_fails():
    problems, _ = ticket("Advances #7")
    assert any("Advances #7" in p for p in problems)


def test_no_reference_at_all_fails():
    problems, _ = ticket("A change with no ticket named.")
    assert any("no canonical ticket reference" in p for p in problems)


def test_branch_numbers_must_appear_in_the_body():
    problems, _ = ticket("CLOSES #7", branch="claude/7-9-two-tickets")
    assert any("ticket 9" in p for p in problems)


def test_shortcode_qualified_branch_numbers_bind_like_bare_ones():
    """skills#147 ruling: branch tokens may carry a lowercase repo
    shortcode before the ticket number (`claude/sk162-session-probe`),
    expressing cross-repo arc identity — one branch name for an arc
    spanning repos. The gate binds on the trailing digits."""
    problems, _ = ticket("ADVANCES #162", branch="claude/sk162-session-probe")
    assert problems == []

    problems, _ = ticket("CLOSES #7", branch="claude/sk7-meta9-two-repos")
    assert any("ticket 9" in p for p in problems)


def test_shortcode_with_digits_still_yields_the_trailing_number():
    """`d10e` is a real shortcode containing digits — the ticket number
    is the token's trailing digit run, never the shortcode's."""
    problems, _ = ticket("CLOSES #9", branch="claude/d10e76-browser-check")
    assert any("ticket 76" in p for p in problems)


def test_non_claude_branch_carries_no_branch_constraint():
    problems, _ = ticket("CLOSES #7", branch="chore/template-update-v9.9.9")
    assert problems == []


def test_branch_pattern_and_marker_come_from_the_central_file():
    custom = dict(KEYWORDS, branch_pattern=r"agent/(\d+)-")
    problems, _ = gate.ticket_violations("CLOSES #7", "agent/9-thing", [], "x", custom)
    assert any("ticket 9" in p for p in problems)
    threads = [thread("pando-genet", ("pando-ramet", "Kein Commit: doc-only."))]
    assert (
        gate.review_violations(
            threads, "pando-ramet", SHAS, "https://github.com", marker="Kein Commit:"
        )
        == []
    )


def test_automated_escape_is_bot_only():
    _, escaped = ticket("", labels=["automated"], author="pandoscope-release-bot[bot]")
    assert escaped
    problems, escaped = ticket("", labels=["automated"], author="pando-ramet")
    assert not escaped and problems


# ----------------------------------------------- premature close (#150)


def test_closing_refs_come_from_the_central_files_closing_tag():
    body = "CLOSES #5, FIXES pandoscope/skills#9, ADVANCES #7"
    refs = gate.closing_refs(body, KEYWORDS, "pandoscope/meta")
    assert refs == ["pandoscope/meta#5", "pandoscope/skills#9"]


def test_bare_refs_normalize_and_duplicates_collapse():
    body = "CLOSES #5 and again CLOSES pandoscope/meta#5"
    assert gate.closing_refs(body, KEYWORDS, "pandoscope/meta") == ["pandoscope/meta#5"]
    assert gate.closing_refs("ADVANCES #5", KEYWORDS, "pandoscope/meta") == []
    assert gate.closing_refs(None, KEYWORDS, "pandoscope/meta") == []


def cross_ref(repo, number, state="open", pr=True):
    issue = {
        "state": state,
        "number": number,
        "repository": {"full_name": repo},
    }
    if pr:
        issue["pull_request"] = {}
    return {"event": "cross-referenced", "source": {"issue": issue}}


def test_open_prs_of_keeps_only_other_open_prs():
    events = [
        cross_ref("pandoscope/meta", 66),
        cross_ref("pandoscope/skills", 113),
        cross_ref("pandoscope/skills", 112, state="closed"),
        cross_ref("pandoscope/ghx", 4, pr=False),
        {"event": "labeled"},
        cross_ref("pandoscope/skills", 113),
    ]
    assert gate.open_prs_of(events, exclude="pandoscope/meta#66") == [
        "pandoscope/skills#113"
    ]


def test_warning_names_the_open_prs_and_notices_never_fail(capsys, monkeypatch):
    monkeypatch.setattr(
        gate, "paginate", lambda path, token: [cross_ref("pandoscope/skills", 113)]
    )
    gate.warn_premature_close("CLOSES #5", KEYWORDS, "pandoscope/meta", 66, "tok")
    out = capsys.readouterr().out
    assert "::warning::" in out and "pandoscope/skills#113" in out

    gate.warn_premature_close("CLOSES #5", KEYWORDS, "pandoscope/meta", 66, None)
    assert "::notice::" in capsys.readouterr().out

    def boom(path, token):
        raise OSError("404")

    monkeypatch.setattr(gate, "paginate", boom)
    gate.warn_premature_close("CLOSES #5", KEYWORDS, "pandoscope/meta", 66, "tok")
    out = capsys.readouterr().out
    assert "::notice::" in out and "::warning::" not in out


def test_no_closing_ref_asks_nothing_of_the_network(monkeypatch):
    def boom(path, token):
        raise AssertionError("the network must not be touched")

    monkeypatch.setattr(gate, "paginate", boom)
    gate.warn_premature_close("ADVANCES #5", KEYWORDS, "pandoscope/meta", 66, "tok")


# ------------------------------------------------------------ reviews


def thread(opener, *replies, path="core.mjs"):
    comments = [{"id": 1, "user": {"login": opener}, "body": "concern", "path": path}]
    comments += [
        {"id": i + 2, "in_reply_to_id": 1, "user": {"login": login}, "body": body}
        for i, (login, body) in enumerate(replies)
    ]
    return comments


SHAS = ["5e1f03edb10baf9e7ad0dfa8d9c36dfdc055a13b"]


def test_verified_commit_url_answers_a_thread():
    threads = [
        thread(
            "pando-genet",
            (
                "pando-ramet",
                "Fixed in https://github.com/o/r/commit/5e1f03edb10baf9e7ad0dfa8d9c36dfdc055a13b",
            ),
        )
    ]
    assert (
        gate.review_violations(
            threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
        )
        == []
    )


def test_any_spelling_of_a_pr_commit_answers():
    """The gate holds the PR's shas; matching one is the proof (#205)."""
    for body in (
        "Fixed in 5e1f03e",
        "Fixed in https://github.com/o/r/pull/9/commits/5e1f03edb10baf9e7ad0dfa8d9c36dfdc055a13b",
        "see 5e1f03edb10baf9e7ad0dfa8d9c36dfdc055a13b (folded)",
    ):
        threads = [thread("pando-genet", ("pando-ramet", body))]
        assert (
            gate.review_violations(
                threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
            )
            == []
        ), body


def test_a_sha_not_on_the_pr_does_not_answer_however_wrapped():
    for body in (
        "Fixed in https://github.com/o/r/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "Fixed in aaaaaaa",
        "the value 0xdeadbeef is a bitmask, not a commit",
        "Fixed in 5e1f03",  # six digits: below the floor, not a sha
    ):
        threads = [thread("pando-genet", ("pando-ramet", body))]
        assert gate.review_violations(
            threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
        ), body


def test_no_commit_marker_answers_but_loose_wording_does_not():
    marked = [thread("pando-genet", ("pando-ramet", "No commit: doc-only concern."))]
    loose = [thread("pando-genet", ("pando-ramet", "no commit was needed here"))]
    assert (
        gate.review_violations(
            marked, "pando-ramet", SHAS, "https://github.com", "No commit:"
        )
        == []
    )
    assert gate.review_violations(
        loose, "pando-ramet", SHAS, "https://github.com", "No commit:"
    )


def test_own_and_bot_threads_are_not_gated():
    threads = [thread("pando-ramet"), thread("some-scanner[bot]")]
    assert (
        gate.review_violations(
            threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
        )
        == []
    )


def test_a_reply_from_someone_else_does_not_answer():
    threads = [
        thread(
            "pando-genet",
            (
                "pando-other",
                "https://github.com/o/r/commit/5e1f03edb10baf9e7ad0dfa8d9c36dfdc055a13b",
            ),
        )
    ]
    assert gate.review_violations(
        threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
    )


def test_resolved_thread_is_off_the_worklist():
    """The reviewer's resolution exempts a thread, answered or not."""
    threads = [thread("pando-genet")]
    assert (
        gate.review_violations(
            threads,
            "pando-ramet",
            SHAS,
            "https://github.com",
            "No commit:",
            resolved={1},
        )
        == []
    )
    # And an unrelated resolved id exempts nothing.
    assert gate.review_violations(
        threads,
        "pando-ramet",
        SHAS,
        "https://github.com",
        "No commit:",
        resolved={999},
    )


def test_violation_names_the_exact_thread():
    """The reader is the agent deciding what to do next, so the message
    carries the quote and the URL that identify the thread."""
    comments = [
        {
            "id": 1,
            "user": {"login": "pando-genet"},
            "body": "Should we add a requirement here?",
            "path": "derived/writing-skills/SKILL.md",
            "html_url": "https://github.com/o/r/pull/30#discussion_r1",
        }
    ]
    [problem] = gate.review_violations(
        [comments], "pando-ramet", SHAS, "https://github.com", "No commit:"
    )
    assert '"Should we add a requirement here?"' in problem
    assert "https://github.com/o/r/pull/30#discussion_r1" in problem
    assert "resolves the thread" in problem


def test_resolved_roots_reads_graphql_nodes():
    nodes = [
        {"isResolved": True, "comments": {"nodes": [{"databaseId": 11}]}},
        {"isResolved": False, "comments": {"nodes": [{"databaseId": 22}]}},
        {"isResolved": True, "comments": {"nodes": []}},
    ]
    assert gate.resolved_roots(nodes) == {11}


def test_threads_group_by_root():
    comments = [
        {"id": 1, "user": {"login": "a"}, "body": "x", "path": "f"},
        {"id": 2, "in_reply_to_id": 1, "user": {"login": "b"}, "body": "y"},
        {"id": 3, "user": {"login": "a"}, "body": "z", "path": "g"},
    ]
    assert [len(t) for t in gate.thread_of(comments)] == [2, 1]


# ---------------------------------------------------------- aggregate


def test_pr_workflows_discovered_including_bare_on_and_yaml_1_1_on():
    assert gate.expects_pr_run("on: [pull_request, push]\njobs: {}\n")
    assert gate.expects_pr_run("on:\n  pull_request:\njobs: {}\n")
    assert gate.expects_pr_run(
        "on:\n  pull_request:\n    types: [opened, synchronize]\njobs: {}\n"
    )


def test_narrow_types_and_other_triggers_are_not_awaited():
    assert not gate.expects_pr_run(
        "on:\n  pull_request:\n    types: [opened]\njobs: {}\n"
    )
    assert not gate.expects_pr_run(
        "on:\n  schedule:\n    - cron: '0 0 * * 0'\njobs: {}\n"
    )


def test_red_dependency_makes_the_gate_red_and_skipped_is_not_passed():
    runs = {"wf/a.yml": {"id": 1, "status": "completed"}}
    jobs = {
        1: [
            {"name": "test", "conclusion": "failure"},
            {"name": "lint", "conclusion": "skipped"},
        ]
    }
    pending, failures = gate.aggregate_verdict(["wf/a.yml"], runs, lambda i: jobs[i])
    assert pending == [] and len(failures) == 2


def test_missing_or_running_workflows_are_pending_not_failed():
    runs = {"wf/a.yml": {"id": 1, "status": "in_progress"}}
    pending, failures = gate.aggregate_verdict(
        ["wf/a.yml", "wf/b.yml"], runs, lambda i: []
    )
    assert len(pending) == 2 and failures == []


def test_all_green_run_passes():
    runs = {"wf/a.yml": {"id": 1, "status": "completed"}}
    jobs = {1: [{"name": "test", "conclusion": "success"}]}
    assert gate.aggregate_verdict(["wf/a.yml"], runs, lambda i: jobs[i]) == ([], [])


def test_own_run_judged_by_siblings_and_never_waits_for_itself():
    jobs = [
        {"name": "ci-ok", "status": "in_progress", "conclusion": None},
        {"name": "ticket", "status": "completed", "conclusion": "success"},
        {"name": "review answers", "status": "in_progress", "conclusion": None},
    ]
    pending, failures = gate.own_verdict(jobs)
    assert len(pending) == 1 and failures == []
    jobs[2] = {"name": "review answers", "status": "completed", "conclusion": "failure"}
    pending, failures = gate.own_verdict(jobs)
    assert pending == [] and len(failures) == 1


def test_own_workflow_path_is_static_never_listed():
    """A review-event re-run is invisible to an event=pull_request run
    listing, so the gate's own path must come from GITHUB_WORKFLOW_REF —
    a lookup through the listing misses itself and judges its own
    workflow by a sibling run the concurrency group already cancelled.
    """
    ref = "o/r/.github/workflows/ci-ok.yml@refs/pull/146/merge"
    assert gate.own_workflow_path(ref, "o/r") == ".github/workflows/ci-ok.yml"


# ----------------------------------------------------------- approval


def review(user, state, commit="headsha", when=1):
    return {"user": {"login": user}, "state": state, "commit_id": commit, "when": when}


def test_current_approval_from_an_approver_passes():
    problems, escaped = gate.approval_violations(
        [review("approver", "APPROVED")], ["approver"], "bot[bot]", [], "headsha"
    )
    assert problems == [] and not escaped


def test_stale_approval_names_both_commits():
    problems, _ = gate.approval_violations(
        [review("approver", "APPROVED", commit="oldsha1")],
        ["approver"],
        "x",
        [],
        "headsha1",
    )
    assert len(problems) == 1
    assert "stale" in problems[0]
    assert "oldsha1" in problems[0] and "headsha" in problems[0]


def test_latest_review_wins_and_comments_do_not_overwrite():
    reviews = [
        review("approver", "APPROVED", commit="oldsha"),
        review("approver", "CHANGES_REQUESTED"),
        review("approver", "COMMENTED"),
    ]
    problems, _ = gate.approval_violations(reviews, ["approver"], "x", [], "headsha")
    assert any("requested changes" in p for p in problems)
    reviews.append(review("approver", "APPROVED"))
    problems, _ = gate.approval_violations(reviews, ["approver"], "x", [], "headsha")
    assert problems == []


def test_non_approver_reviews_do_not_count():
    problems, _ = gate.approval_violations(
        [review("stranger", "APPROVED")], ["approver"], "x", [], "headsha"
    )
    assert any("no approving review" in p for p in problems)


def test_approver_author_passes_without_a_review():
    problems, escaped = gate.approval_violations(
        [], ["approver"], "approver", [], "headsha"
    )
    assert problems == [] and not escaped


def test_automated_escape_is_bot_only_for_approval_too():
    _, escaped = gate.approval_violations(
        [], ["approver"], "updater[bot]", ["automated"], "headsha"
    )
    assert escaped
    problems, escaped = gate.approval_violations(
        [], ["approver"], "human", ["automated"], "headsha"
    )
    assert problems and not escaped


def test_empty_approver_list_is_loud(tmp_path, monkeypatch):
    config = tmp_path / "merge-approvers.json"
    config.write_text('{"approvers": []}')
    monkeypatch.setenv("MERGE_APPROVERS", str(config))
    try:
        gate.approvers_config()
    except ValueError as err:
        assert "no approvers" in str(err)
    else:
        raise AssertionError("an empty approver list must refuse, not pass")


# -------------------------------------------------------------- rerun


def gate_run(run_id, path=".github/workflows/ci-ok.yml", **overrides):
    run = {"id": run_id, "path": path, "status": "completed", "conclusion": "failure"}
    run.update(overrides)
    return run


def test_stale_gate_runs_are_completed_non_green_gate_runs_oldest_first():
    runs = [
        gate_run(3, conclusion="success"),
        gate_run(2, conclusion="cancelled"),
        gate_run(5),
        gate_run(4, path=".github/workflows/ci.yml"),
    ]
    stale = gate.stale_gate_runs(runs, ".github/workflows/ci-ok.yml")
    assert [run["id"] for run in stale] == [2, 5]


def test_a_skipped_gate_run_is_not_stale():
    """`skipped` passes required-check evaluation, so there is no red
    to supersede — re-running it would only spend Actions minutes."""
    runs = [gate_run(1, conclusion="skipped")]
    assert gate.stale_gate_runs(runs, ".github/workflows/ci-ok.yml") == []


def test_an_in_flight_gate_run_is_not_stale_and_reports_as_busy():
    """A queued or running gate run is about to publish the current
    verdict — re-running under it would race the concurrency group and
    cancel it, which is the failure mode the separate workflow exists
    to avoid (#190)."""
    runs = [
        gate_run(1, status="in_progress", conclusion=None),
        gate_run(2, status="queued", conclusion=None),
        gate_run(3, path=".github/workflows/ci.yml", status="queued", conclusion=None),
    ]
    assert gate.stale_gate_runs(runs, ".github/workflows/ci-ok.yml") == []
    assert gate.gate_busy(runs, ".github/workflows/ci-ok.yml")
    assert not gate.gate_busy([gate_run(1)] + runs[2:], ".github/workflows/ci-ok.yml")


# -------------------------------------------------------------- leaks


def test_parse_blocklist_splits_on_pipes_and_drops_blanks():
    assert gate.parse_blocklist("alice|Bob Example| |bob@example.org|") == [
        ("alice", "entry 1"),
        ("Bob Example", "entry 2"),
        ("bob@example.org", "entry 4"),
    ]
    assert gate.parse_blocklist("") == []
    assert gate.parse_blocklist(None) == []


def test_parse_blocklist_reads_explicit_placeholder_labels():
    """An entry may name its own placeholder (`value=pb:name`), so the
    label survives list edits — a positional 'entry N' drifts the
    moment a term is inserted above it."""
    assert gate.parse_blocklist("alice=pb:old-account|bob|carol=pb:nickname") == [
        ("alice", "pb:old-account"),
        ("bob", "entry 2"),
        ("carol", "pb:nickname"),
    ]


def test_leak_violations_name_surface_and_entry_never_the_value():
    """The denylist values are themselves the identifying material, and
    a CI log on a public repo is a public surface — so a violation names
    WHERE and WHICH entry, never WHAT (#189)."""
    surfaces = [("PR title", "ship it"), ("commit 3f2a1b0 message", "by alice")]
    values = gate.parse_blocklist("alice=pb:old-account|bob")
    problems = gate.leak_violations(surfaces, values)
    assert len(problems) == 1
    assert "commit 3f2a1b0 message" in problems[0]
    assert "pb:old-account" in problems[0]
    assert "alice" not in problems[0]


def test_leak_violations_match_case_insensitively():
    problems = gate.leak_violations(
        [("PR body", "Mail ALICE@example.org")], gate.parse_blocklist("alice")
    )
    assert len(problems) == 1
    assert "entry 1" in problems[0]


def test_leak_violations_report_every_hit_pair_once():
    surfaces = [("PR title", "alice and bob"), ("PR body", "bob, bob, bob")]
    problems = gate.leak_violations(surfaces, gate.parse_blocklist("alice|bob"))
    assert len(problems) == 3


def test_empty_blocklist_finds_nothing():
    assert gate.leak_violations([("PR body", "anything at all")], []) == []


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
        gate.pr_surfaces(
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
    refs = gate.referenced_tickets(body, "pandoscope/meta")
    assert refs == ["pandoscope/meta#7", "pandoscope/skills#9"]
    assert gate.referenced_tickets(None, "pandoscope/meta") == []


def test_pr_surfaces_include_branch_name_and_referenced_tickets():
    """The branch name publishes with the PR; referenced tickets are
    already public, so a hit there is red-and-loud at the gate rather
    than prevention — the principal fixes the ticket, the PR unblocks."""
    pr = {"title": "t", "body": "b", "head": {"ref": "claude/7-thing"}}
    tickets = [("o/r#5", "ticket body", [{"id": 3, "body": "a comment"}])]
    surfaces = dict(gate.pr_surfaces(pr, [], [], tickets=tickets))
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
    surfaces = dict(gate.pr_surfaces({"title": "t", "body": "b"}, [], files))
    surface = surfaces["diff of tests/test_x.py"]
    assert "nobody@example.org" in surface
    assert "alice" not in surface
    assert "+++" not in surface
    assert gate.leak_violations(
        [("diff of tests/test_x.py", surface)], gate.parse_blocklist("alice")
    ) == []


def test_diff_surface_survives_a_missing_patch():
    """Binary and oversized files arrive without a patch; that is no
    surface, not a crash."""
    surfaces = dict(gate.pr_surfaces({}, [], [{"filename": "img.png"}]))
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


# ------------------------------------------------- copies stay pinned


def test_gate_script_copies_are_byte_identical_template_first():
    source = TEMPLATE_COPY.read_bytes()
    for copy in (
        ROOT / "scripts" / "ci" / "check_gate.py",
        ROOT / "decision-memory" / "scripts" / "ci" / "check_gate.py",
        ROOT / "evidence-memory" / "scripts" / "ci" / "check_gate.py",
    ):
        assert copy.read_bytes() == source, f"{copy} drifted from the template copy"


def test_store_keyword_files_are_byte_identical_to_the_template():
    source = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "reference-keywords.json"
    ).read_bytes()
    for copy in (
        ROOT / ".github" / "reference-keywords.json",
        ROOT / "decision-memory" / ".github" / "reference-keywords.json",
        ROOT / "evidence-memory" / ".github" / "reference-keywords.json",
    ):
        assert copy.read_bytes() == source, f"{copy} drifted from the template copy"


def test_store_gate_workflows_are_identical_and_unticketed():
    dm = (
        ROOT / "decision-memory" / ".github" / "workflows" / "ci-ok.yml.jinja"
    ).read_text()
    em = (
        ROOT / "evidence-memory" / ".github" / "workflows" / "ci-ok.yml.jinja"
    ).read_text()
    assert dm == em
    assert "ticket" not in dm.split("jobs:")[1].split("review-answers")[0]
    template = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "ci-ok.yml.jinja"
    ).read_text()
    assert "check_gate.py ticket" in template
    # The comments carry the central file's ACTUAL values, injected at
    # render time — the rendered root copy shows the marker itself and
    # no jinja residue, so readers never chase an indirection.
    assert "reference_keywords()" in template
    root_copy = (ROOT / ".github" / "workflows" / "ci-ok.yml").read_text()
    assert KEYWORDS["no_commit_marker"] in root_copy
    assert "{%" not in root_copy and "reference_keywords" not in root_copy


def test_the_gate_judges_the_live_body_not_the_event_payload(
    tmp_path, monkeypatch, capsys
):
    """A re-run replays the payload the run was created with (#159).

    GitHub's re-run hands the job the ORIGINAL event, so a body fixed
    after the first failure is invisible to it and the gate fails for
    a reason that no longer exists — permanently, since every re-run
    replays the same stale payload. The body therefore comes from the
    live PR; only the number comes from the event.
    """
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 155,
                    "body": "no reference here",
                    "head": {"ref": "claude/159-thing"},
                    "labels": [],
                    "user": {"login": "pando-ramet"},
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "pandoscope/agentic-engineering-template")
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv(
        "REFERENCE_KEYWORDS",
        str(
            ROOT
            / "template"
            / "{% if agentic_forge == 'github' %}.github{% endif %}"
            / "reference-keywords.json"
        ),
    )
    monkeypatch.setattr(
        gate,
        "fetch",
        lambda path, token: {
            "number": 155,
            "body": "CLOSES #159",
            "head": {"ref": "claude/159-thing"},
            "labels": [],
            "user": {"login": "pando-ramet"},
        },
    )
    monkeypatch.setattr(gate, "paginate", lambda path, token: [])
    assert gate.run_ticket() == 0
    assert "::error::" not in capsys.readouterr().out


# ------------------------------------------------------------ payload


def test_payload_surfaces_read_only_the_item_the_event_carries():
    """#208: the event-driven net scans just the one thing that
    changed — an issue's title and body, or a comment's body — labeled
    with its link so a hit says where without walking the repo."""
    issue_event = {
        "action": "edited",
        "issue": {"title": "t", "body": "b", "html_url": "https://x/issues/1"},
    }
    assert gate.payload_surfaces(issue_event) == [
        ("issue https://x/issues/1 title", "t"),
        ("issue https://x/issues/1 body", "b"),
    ]
    comment_event = {
        "action": "created",
        "issue": {"title": "t", "body": "b", "html_url": "https://x/issues/1"},
        "comment": {"body": "c", "html_url": "https://x/issues/1#issuecomment-5"},
    }
    assert gate.payload_surfaces(comment_event) == [
        ("comment https://x/issues/1#issuecomment-5", "c"),
    ]
    review_comment_event = {
        "action": "edited",
        "pull_request": {"title": "t", "body": "b", "html_url": "https://x/pull/2"},
        "comment": {
            "body": "rc",
            "path": "a.py",
            "html_url": "https://x/pull/2#discussion_r7",
        },
    }
    assert gate.payload_surfaces(review_comment_event) == [
        ("comment https://x/pull/2#discussion_r7 on a.py", "rc"),
    ]


def test_run_payload_is_red_on_a_hit_value_silent_and_green_when_clean(
    tmp_path, monkeypatch, capsys
):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "action": "created",
                "issue": {"title": "t", "body": "b", "html_url": "https://x/issues/1"},
                "comment": {
                    "body": "ping sekritvalue9 please",
                    "html_url": "https://x/issues/1#issuecomment-5",
                },
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("PUSH_BLOCKLIST", "sekritvalue9=pb:old-pw|other")
    assert gate.run_payload() == 1
    out = capsys.readouterr().out
    assert "::error" in out
    assert "pb:old-pw" in out
    assert "https://x/issues/1#issuecomment-5" in out
    assert "sekritvalue9" not in out

    monkeypatch.setenv("PUSH_BLOCKLIST", "other")
    assert gate.run_payload() == 0
    assert "::error" not in capsys.readouterr().out


def test_run_payload_warns_loudly_on_an_empty_blocklist(monkeypatch, capsys):
    monkeypatch.setenv("PUSH_BLOCKLIST", "")
    assert gate.run_payload() == 0
    assert "::warning" in capsys.readouterr().out


def test_payload_scan_workflow_fires_on_every_comment_shaped_event():
    """The instant-detection piece (#208): issues, issue comments (PR
    conversation included) and review comments, on create AND edit —
    a comment scrubbed by edit is also a comment re-leaked by edit.
    Free on public repos; private repos are not exposed and skip via
    the event's own repository flag, no API call needed."""
    text = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "payload-scan.yml.jinja"
    ).read_text()
    for trigger in ("issues:", "issue_comment:", "pull_request_review_comment:"):
        assert trigger in text
    assert text.count("- created") == 2
    assert text.count("- edited") == 3
    assert "- opened" in text
    assert "check_gate.py payload" in text
    assert "secrets.PUSH_BLOCKLIST" in text
    assert "github.event.repository.private" in text
    assert "runs-on: {{ agentic_actions_runner }}" in text


def test_root_stamp_carries_the_payload_scan_workflow():
    text = (ROOT / ".github" / "workflows" / "payload-scan.yml").read_text()
    assert "{%" not in text
    assert "runs-on: ubuntu-latest" in text


# ------------------------------------------------------- template drift


def test_drift_check_fails_loudly_on_claude_md_drift():
    """Measured on pandoscope/disambiguate#82: `copier recopy` asks
    "Overwrite CLAUDE.md? (Y/n)" wherever that file diverged, and a
    non-interactive runner dies there before the diff — the drift
    check never reached its verdict. --overwrite gets past the prompt;
    the verdict then covers CLAUDE.md like every other stamped file.
    Ruled on #213: CLAUDE.md must not drift in any repo, so no restore
    exempts it (#199)."""
    text = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "lint.yml.jinja"
    ).read_text()
    recopy = text[text.index("copier recopy") : text.index("git status --porcelain")]
    assert "--overwrite" in recopy
    assert "git checkout -- CLAUDE.md" not in text


def test_drift_check_blanks_installer_owned_lock_hashes():
    """Measured on pandoscope/disambiguate#82 (#214): the post-render
    skill installer recomputes every computedHash in skills-lock.json,
    so the stamped hashes are stale by construction in every consumer.
    The verdict compares the lock with those fields blanked — skill
    set, sources and paths still fail loudly; the one installer-owned
    field does not."""
    text = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "lint.yml.jinja"
    ).read_text()
    step = text[text.index("copier recopy") : text.index("git status --porcelain")]
    assert "computedHash" in step
    assert "skills-lock.json" in step
