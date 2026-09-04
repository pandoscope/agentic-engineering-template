"""The `approval` subcommand: an allowlisted human's latest review
approves the current head commit, or authored the PR (#187).
"""

from tests.gate_support import (
    gate_approval,
)


def review(user, state, commit="headsha", when=1):
    return {"user": {"login": user}, "state": state, "commit_id": commit, "when": when}


def test_current_approval_from_an_approver_passes():
    problems, escaped = gate_approval.approval_violations(
        [review("approver", "APPROVED")], ["approver"], "bot[bot]", [], "headsha"
    )
    assert problems == [] and not escaped


def test_stale_approval_names_both_commits():
    problems, _ = gate_approval.approval_violations(
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
    problems, _ = gate_approval.approval_violations(
        reviews, ["approver"], "x", [], "headsha"
    )
    assert any("requested changes" in p for p in problems)
    reviews.append(review("approver", "APPROVED"))
    problems, _ = gate_approval.approval_violations(
        reviews, ["approver"], "x", [], "headsha"
    )
    assert problems == []


def test_non_approver_reviews_do_not_count():
    problems, _ = gate_approval.approval_violations(
        [review("stranger", "APPROVED")], ["approver"], "x", [], "headsha"
    )
    assert any("no approving review" in p for p in problems)


def test_approver_author_passes_without_a_review():
    problems, escaped = gate_approval.approval_violations(
        [], ["approver"], "approver", [], "headsha"
    )
    assert problems == [] and not escaped


def test_automated_escape_is_bot_only_for_approval_too():
    _, escaped = gate_approval.approval_violations(
        [], ["approver"], "updater[bot]", ["automated"], "headsha"
    )
    assert escaped
    problems, escaped = gate_approval.approval_violations(
        [], ["approver"], "human", ["automated"], "headsha"
    )
    assert problems and not escaped


def test_empty_approver_list_is_loud(tmp_path, monkeypatch):
    config = tmp_path / "merge-approvers.json"
    config.write_text('{"approvers": []}')
    monkeypatch.setenv("MERGE_APPROVERS", str(config))
    try:
        gate_approval.approvers_config()
    except ValueError as err:
        assert "no approvers" in str(err)
    else:
        raise AssertionError("an empty approver list must refuse, not pass")
