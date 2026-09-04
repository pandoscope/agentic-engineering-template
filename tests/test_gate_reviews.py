"""The `reviews` subcommand: a human review thread is answered by a
verified commit URL, the central file's `no_commit_marker`, or the
reviewer resolving it (#137).
"""

from tests.gate_support import (
    gate_reviews,
)


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
        gate_reviews.review_violations(
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
            gate_reviews.review_violations(
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
        assert gate_reviews.review_violations(
            threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
        ), body


def test_no_commit_marker_answers_but_loose_wording_does_not():
    marked = [thread("pando-genet", ("pando-ramet", "No commit: doc-only concern."))]
    loose = [thread("pando-genet", ("pando-ramet", "no commit was needed here"))]
    assert (
        gate_reviews.review_violations(
            marked, "pando-ramet", SHAS, "https://github.com", "No commit:"
        )
        == []
    )
    assert gate_reviews.review_violations(
        loose, "pando-ramet", SHAS, "https://github.com", "No commit:"
    )


def test_own_and_bot_threads_are_not_gated():
    threads = [thread("pando-ramet"), thread("some-scanner[bot]")]
    assert (
        gate_reviews.review_violations(
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
    assert gate_reviews.review_violations(
        threads, "pando-ramet", SHAS, "https://github.com", "No commit:"
    )


def test_resolved_thread_is_off_the_worklist():
    """The reviewer's resolution exempts a thread, answered or not."""
    threads = [thread("pando-genet")]
    assert (
        gate_reviews.review_violations(
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
    assert gate_reviews.review_violations(
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
    [problem] = gate_reviews.review_violations(
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
    assert gate_reviews.resolved_roots(nodes) == {11}


def test_threads_group_by_root():
    comments = [
        {"id": 1, "user": {"login": "a"}, "body": "x", "path": "f"},
        {"id": 2, "in_reply_to_id": 1, "user": {"login": "b"}, "body": "y"},
        {"id": 3, "user": {"login": "a"}, "body": "z", "path": "g"},
    ]
    assert [len(t) for t in gate_reviews.thread_of(comments)] == [2, 1]


def test_no_commit_marker_comes_from_the_central_file():
    """#144: the marker is passed in, never defaulted here. The ticket
    half of the same claim is `test_branch_pattern_comes_from_the_central_file`."""
    threads = [thread("pando-genet", ("pando-ramet", "Kein Commit: doc-only."))]
    assert (
        gate_reviews.review_violations(
            threads, "pando-ramet", SHAS, "https://github.com", marker="Kein Commit:"
        )
        == []
    )
