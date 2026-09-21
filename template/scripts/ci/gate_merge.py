"""The `merge` subcommand (bot-merge.yml, agentic-engineering-template#260):
a private repo's PR is merged by the release bot once every job of every
PR workflow on its head succeeded. Not one of ci-ok.yml's jobs.

Ruled on pandoscope/meta#132: GitHub Free enforces no rulesets on
private repos, so ci-ok is advisory there and the merge button works on
red. Repository permissions are the control the plan still honours, so
humans hold no write to main and this job, acting as the bot, is the one
path in. It re-judges live data with the aggregate's own verdict and
never fabricates green: a PR the gate rejects stays open.
"""

import base64
import os
import time
import urllib.error

from gate_aggregate import aggregate_verdict, expects_pr_run
from gate_api import fetch, paginate, put_json

SWITCH = "BOT_MERGE_ENABLED"
WORKFLOWS = ".github/workflows"
PERMISSIONS = "contents: write and pull_requests: write"


def eligibility(repo_private, switch):
    """Why this repository is not one the bot merges in, or None.

    Two conditions, both stated so a run that does nothing says which
    one held it back: the repository is private (a public repo keeps
    its rulesets, and this job must never double as a second gate
    there), and the org variable BOT_MERGE_ENABLED is "true" (meta's
    credential sync sets it on exactly the private repos that need
    branch protection; session-memory is excluded there by name).
    """
    if str(repo_private).lower() != "true":
        return "repository is public — rulesets gate it, the bot does not merge here"
    if str(switch).lower() != "true":
        return f"{SWITCH} is not 'true' — the bot does not merge in this repository"
    return None


def merge_candidates(pulls, head_sha):
    """The open, non-draft PRs whose head is `head_sha`, oldest first.

    A draft is the author saying "not yet", which no green overrides. A
    PR whose head moved on is judged by its next event, not this one.
    """
    return sorted(
        (
            pr
            for pr in pulls
            if pr.get("state") == "open"
            and not pr.get("draft")
            and (pr.get("head") or {}).get("sha") == head_sha
        ),
        key=lambda pr: pr["number"],
    )


def expected_workflows(repo, sha, token):
    """The PR workflows of the head under judgement, read from the head.

    A workflow_run fires on the default branch and checks that out, so
    the files on disk are not the head's: a workflow the head removed
    would be awaited forever and one it added never. The contents API at
    the head SHA is the head.
    """
    expected = []
    for entry in fetch(f"/repos/{repo}/contents/{WORKFLOWS}?ref={sha}", token):
        if not entry["name"].endswith((".yml", ".yaml")):
            continue
        blob = fetch(f"/repos/{repo}/contents/{entry['path']}?ref={sha}", token)
        text = base64.b64decode(blob["content"]).decode("utf-8")
        if expects_pr_run(text):
            expected.append(entry["path"])
    return sorted(expected)


def run_merge():
    """Merge each candidate PR once its head is green; exit 0 on every
    orderly path. A red head is the gate's verdict, not this job's
    failure, and a still-pending head is left for the next event."""
    token = os.environ["GH_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["HEAD_SHA"]
    reason = eligibility(os.environ.get("REPO_PRIVATE", ""), os.environ.get(SWITCH, ""))
    if reason:
        print(f"Not merging: {reason}.")
        return 0
    deadline = time.monotonic() + int(os.environ.get("BOT_MERGE_TIMEOUT", "1500"))

    pulls = fetch(f"/repos/{repo}/commits/{sha}/pulls", token)
    candidates = merge_candidates(pulls, sha)
    if not candidates:
        print(f"Not merging: no open non-draft PR has head {sha[:7]}.")
        return 0

    expected = expected_workflows(repo, sha, token)

    def jobs_of(run_id):
        return paginate(f"/repos/{repo}/actions/runs/{run_id}/jobs", token, "jobs")

    while True:
        # Every run at this head, whatever event produced it: the push's
        # run has `merge approval` red for good, and the approval's own
        # pull_request_review run is the live verdict. Newest per
        # workflow wins.
        listed = paginate(
            f"/repos/{repo}/actions/runs?head_sha={sha}", token, "workflow_runs"
        )
        runs = {}
        for run in sorted(listed, key=lambda r: r["id"]):
            runs[run["path"]] = run
        pending, failures = aggregate_verdict(expected, runs, jobs_of)
        if failures:
            for failure in failures:
                print(f"Not merging: {failure}")
            return 0
        if not pending:
            break
        if time.monotonic() > deadline:
            print("::warning::PR workflows still pending at the bot-merge timeout.")
            return 0
        print("waiting:", "; ".join(pending))
        time.sleep(15)

    for pr in candidates:
        number = pr["number"]
        try:
            status = put_json(
                f"/repos/{repo}/pulls/{number}/merge",
                {"merge_method": "merge", "sha": sha},
                token,
            )
        except urllib.error.HTTPError as error:
            # A refused merge is the bot's own failure, not the gate's
            # verdict: the token lacks a permission, or the PR changed
            # under us. The permission is named first (#260).
            print(
                f"::error::Merge of #{number} refused — a missing permission: the "
                f"release bot needs {PERMISSIONS} on this repository "
                f"(HTTP {error.code})."
            )
            return 1
        print(f"Merged #{number} at {sha[:7]} (HTTP {status}).")
    return 0
