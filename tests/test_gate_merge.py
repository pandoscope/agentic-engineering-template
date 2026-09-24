"""The `merge` subcommand (#260): the bot merges a private repo's green
PR. It merges nothing on a public repo, with the switch unset, for a
draft, or at a red head.
"""

import base64
import urllib.error

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


def contents(path):
    """The head's `.github/workflows` as the contents API serves it."""
    return {
        "name": "ci.yml",
        "path": ".github/workflows/ci.yml",
        "content": base64.b64encode(b"on:\n  pull_request:\njobs: {}\n").decode(),
        "encoding": "base64",
    }


def wire(monkeypatch, tmp_path, runs, jobs, merged, files=None):
    """Wire up a private, switched-on repo. Its head carries `files`
    (default: one PR workflow), and the API answers with `runs` and
    `jobs`."""
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
    files = [contents(".github/workflows/ci.yml")] if files is None else files

    def fetch(path, token):
        if "/commits/abc/pulls" in path:
            return [pr(3), pr(4, draft=True)]
        if path.endswith("/contents/.github/workflows?ref=abc"):
            return [{k: f[k] for k in ("name", "path")} for f in files]
        for f in files:
            if path == f"/repos/o/r/contents/{f['path']}?ref=abc":
                return f
        raise AssertionError(f"unexpected fetch {path}")

    monkeypatch.setattr(gate_merge, "fetch", fetch)
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


def test_run_merges_only_a_green_head(monkeypatch, tmp_path, capsys):
    """The bot takes the aggregate's own verdict. A red `merge approval`
    holds the merge, and the log names it. A green board merges every
    candidate once, by head SHA."""
    runs = [{"id": 1, "path": ".github/workflows/ci.yml", "status": "completed"}]
    jobs = [{"name": "merge approval", "status": "completed", "conclusion": "success"}]
    merged = []
    wire(monkeypatch, tmp_path, runs, jobs, merged)

    assert gate_merge.run_merge() == 0
    assert merged == [
        ("/repos/o/r/pulls/3/merge", {"merge_method": "merge", "sha": "abc"})
    ]

    merged.clear()
    jobs[0]["conclusion"] = "failure"
    assert gate_merge.run_merge() == 0
    assert merged == []
    assert "merge approval" in capsys.readouterr().out


def test_the_newest_run_per_workflow_is_judged_whatever_its_event(
    monkeypatch, tmp_path
):
    """An approval re-runs the gate under pull_request_review, and the
    push's run stays red forever. The bot judges the live run, so it
    merges a PR approved after its last push."""
    runs = [
        {
            "id": 1,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "event": "pull_request",
        },
        {
            "id": 2,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "event": "pull_request_review",
        },
    ]
    jobs_by_run = {
        1: [{"name": "merge approval", "conclusion": "failure"}],
        2: [{"name": "merge approval", "conclusion": "success"}],
    }
    merged = []
    wire(monkeypatch, tmp_path, runs, [], merged)
    monkeypatch.setattr(
        gate_merge,
        "paginate",
        lambda path, token, key=None: (
            runs
            if "actions/runs?" in path
            else jobs_by_run[int(path.split("/runs/")[1].split("/")[0])]
        ),
    )
    assert gate_merge.run_merge() == 0
    assert [p for p, _ in merged] == ["/repos/o/r/pulls/3/merge"]


def test_expected_workflows_come_from_the_head_not_the_checkout(monkeypatch, tmp_path):
    """workflow_run checks out the default branch. The bot must not
    await a workflow that the head removed, and must await one that the
    head added."""
    checkout = tmp_path / ".github" / "workflows"
    checkout.mkdir(parents=True)
    (checkout / "gone.yml").write_text("on:\n  pull_request:\njobs: {}\n")
    runs = [{"id": 1, "path": ".github/workflows/ci.yml", "status": "completed"}]
    jobs = [{"name": "test", "conclusion": "success"}]
    merged = []
    wire(monkeypatch, tmp_path, runs, jobs, merged)
    assert gate_merge.run_merge() == 0
    assert len(merged) == 1


def test_a_refused_merge_names_the_missing_permission(monkeypatch, tmp_path, capsys):
    """The bot's token may lack contents:write or pull_requests:write.
    GitHub then answers the merge with HTTP 403. The failure names the
    permission first."""
    runs = [{"id": 1, "path": ".github/workflows/ci.yml", "status": "completed"}]
    jobs = [{"name": "test", "conclusion": "success"}]
    wire(monkeypatch, tmp_path, runs, jobs, [])

    def refused(path, body, token):
        raise urllib.error.HTTPError(path, 403, "Forbidden", {}, None)

    monkeypatch.setattr(gate_merge, "put_json", refused)
    assert gate_merge.run_merge() == 1
    out = capsys.readouterr().out
    assert "contents: write" in out
    assert "pull_requests: write" in out
    assert out.index("permission") < out.index("403")


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


def test_a_refused_run_listing_names_the_missing_permission(
    monkeypatch, tmp_path, capsys
):
    """Listing the runs at the head needs actions: read on the app
    token. Without it GitHub answers 403, and the bot died on an
    unnamed traceback (#280). The failure names the permission first."""
    wire(monkeypatch, tmp_path, [], [], [])

    def refused(path, token, key=None):
        raise urllib.error.HTTPError(path, 403, "Forbidden", {}, None)

    monkeypatch.setattr(gate_merge, "paginate", refused)
    assert gate_merge.run_merge() == 1
    out = capsys.readouterr().out
    assert "actions: read" in out
    assert out.index("permission") < out.index("403")
