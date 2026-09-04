"""The `ticket` subcommand: the PR names its tickets in the canonical
ALL-CAPS form, and the premature-close warning (#137, #150).
"""

import json

from tests.gate_support import (
    KEYWORDS,
    ROOT,
    gate_ticket,
)


def ticket(body, branch="claude/7-thing", labels=(), author="pando-ramet"):
    return gate_ticket.ticket_violations(body, branch, list(labels), author, KEYWORDS)


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


def test_branch_pattern_comes_from_the_central_file():
    """#144: the pattern is passed in, never defaulted here. The reviews
    half of the same claim is `test_no_commit_marker_comes_from_the_central_file`."""
    custom = dict(KEYWORDS, branch_pattern=r"agent/(\d+)-")
    problems, _ = gate_ticket.ticket_violations(
        "CLOSES #7", "agent/9-thing", [], "x", custom
    )
    assert any("ticket 9" in p for p in problems)


def test_automated_escape_is_bot_only():
    _, escaped = ticket("", labels=["automated"], author="pandoscope-release-bot[bot]")
    assert escaped
    problems, escaped = ticket("", labels=["automated"], author="pando-ramet")
    assert not escaped and problems


# ----------------------------------------------- premature close (#150)


def test_closing_refs_come_from_the_central_files_closing_tag():
    body = "CLOSES #5, FIXES pandoscope/skills#9, ADVANCES #7"
    refs = gate_ticket.closing_refs(body, KEYWORDS, "pandoscope/meta")
    assert refs == ["pandoscope/meta#5", "pandoscope/skills#9"]


def test_bare_refs_normalize_and_duplicates_collapse():
    body = "CLOSES #5 and again CLOSES pandoscope/meta#5"
    assert gate_ticket.closing_refs(body, KEYWORDS, "pandoscope/meta") == [
        "pandoscope/meta#5"
    ]
    assert gate_ticket.closing_refs("ADVANCES #5", KEYWORDS, "pandoscope/meta") == []
    assert gate_ticket.closing_refs(None, KEYWORDS, "pandoscope/meta") == []


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
    assert gate_ticket.open_prs_of(events, exclude="pandoscope/meta#66") == [
        "pandoscope/skills#113"
    ]


def test_warning_names_the_open_prs_and_notices_never_fail(capsys, monkeypatch):
    monkeypatch.setattr(
        gate_ticket,
        "paginate",
        lambda path, token: [cross_ref("pandoscope/skills", 113)],
    )
    gate_ticket.warn_premature_close(
        "CLOSES #5", KEYWORDS, "pandoscope/meta", 66, "tok"
    )
    out = capsys.readouterr().out
    assert "::warning::" in out and "pandoscope/skills#113" in out

    gate_ticket.warn_premature_close("CLOSES #5", KEYWORDS, "pandoscope/meta", 66, None)
    assert "::notice::" in capsys.readouterr().out

    def boom(path, token):
        raise OSError("404")

    monkeypatch.setattr(gate_ticket, "paginate", boom)
    gate_ticket.warn_premature_close(
        "CLOSES #5", KEYWORDS, "pandoscope/meta", 66, "tok"
    )
    out = capsys.readouterr().out
    assert "::notice::" in out and "::warning::" not in out


def test_no_closing_ref_asks_nothing_of_the_network(monkeypatch):
    def boom(path, token):
        raise AssertionError("the network must not be touched")

    monkeypatch.setattr(gate_ticket, "paginate", boom)
    gate_ticket.warn_premature_close(
        "ADVANCES #5", KEYWORDS, "pandoscope/meta", 66, "tok"
    )


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
        gate_ticket,
        "fetch",
        lambda path, token: {
            "number": 155,
            "body": "CLOSES #159",
            "head": {"ref": "claude/159-thing"},
            "labels": [],
            "user": {"login": "pando-ramet"},
        },
    )
    monkeypatch.setattr(gate_ticket, "paginate", lambda path, token: [])
    assert gate_ticket.run_ticket() == 0
    assert "::error::" not in capsys.readouterr().out
