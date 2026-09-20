"""The `merge` subcommand: a private repo's green PR is merged by the
bot; a public repo, an unset switch, a draft or a red head is not (#260).
"""

from tests.gate_support import gate_merge


def test_public_repo_and_unset_switch_are_named_not_silent():
    assert "public" in gate_merge.eligibility("false", "true")
    assert "BOT_MERGE_ENABLED" in gate_merge.eligibility("true", "")
    assert "BOT_MERGE_ENABLED" in gate_merge.eligibility("true", "false")
    assert gate_merge.eligibility("true", "true") is None
    assert gate_merge.eligibility(True, "TRUE") is None


def pr(number, sha="abc", state="open", draft=False):
    return {"number": number, "state": state, "draft": draft, "head": {"sha": sha}}


def test_candidates_are_open_non_draft_prs_at_this_head_oldest_first():
    pulls = [
        pr(7),
        pr(3),
        pr(4, draft=True),
        pr(5, state="closed"),
        pr(6, sha="moved"),
    ]
    assert [p["number"] for p in gate_merge.merge_candidates(pulls, "abc")] == [3, 7]


def test_run_merges_only_a_green_head(monkeypatch, tmp_path):
    """The verdict is the aggregate's own: a red job anywhere holds the
    merge, a green board merges every candidate once, by head SHA."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on:\n  pull_request:\njobs: {}\n")
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "GH_TOKEN": "t",
        "GITHUB_REPOSITORY": "o/r",
        "HEAD_SHA": "abc",
        "REPO_PRIVATE": "true",
        "BOT_MERGE_ENABLED": "true",
        "BOT_MERGE_TIMEOUT": "0",
    }.items():
        monkeypatch.setenv(key, value)

    runs = [{"id": 1, "path": ".github/workflows/ci.yml", "status": "completed"}]
    jobs = [{"name": "test", "status": "completed", "conclusion": "success"}]
    merged = []
    monkeypatch.setattr(
        gate_merge, "fetch", lambda path, token: [pr(3), pr(4, draft=True)]
    )
    monkeypatch.setattr(
        gate_merge,
        "paginate",
        lambda path, token, key=None: runs if "actions/runs?" in path else jobs,
    )
    monkeypatch.setattr(
        gate_merge,
        "put_json",
        lambda path, body, token: merged.append((path, body)) or 200,
    )

    assert gate_merge.run_merge() == 0
    assert merged == [
        ("/repos/o/r/pulls/3/merge", {"merge_method": "merge", "sha": "abc"})
    ]

    merged.clear()
    jobs[0]["conclusion"] = "failure"
    assert gate_merge.run_merge() == 0
    assert merged == []


def test_run_is_inert_on_a_public_repo(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("HEAD_SHA", "abc")
    monkeypatch.setenv("REPO_PRIVATE", "false")
    monkeypatch.setenv("BOT_MERGE_ENABLED", "true")
    calls = []
    monkeypatch.setattr(gate_merge, "fetch", lambda *a: calls.append(a))
    assert gate_merge.run_merge() == 0
    assert calls == []
