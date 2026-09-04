"""The `aggregate` subcommand: every job of every pull_request-triggered
workflow on this head SHA succeeded (#137).
"""

from tests.gate_support import (
    gate_aggregate,
)


def test_pr_workflows_discovered_including_bare_on_and_yaml_1_1_on():
    assert gate_aggregate.expects_pr_run("on: [pull_request, push]\njobs: {}\n")
    assert gate_aggregate.expects_pr_run("on:\n  pull_request:\njobs: {}\n")
    assert gate_aggregate.expects_pr_run(
        "on:\n  pull_request:\n    types: [opened, synchronize]\njobs: {}\n"
    )


def test_narrow_types_and_other_triggers_are_not_awaited():
    assert not gate_aggregate.expects_pr_run(
        "on:\n  pull_request:\n    types: [opened]\njobs: {}\n"
    )
    assert not gate_aggregate.expects_pr_run(
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
    pending, failures = gate_aggregate.aggregate_verdict(
        ["wf/a.yml"], runs, lambda i: jobs[i]
    )
    assert pending == [] and len(failures) == 2


def test_missing_or_running_workflows_are_pending_not_failed():
    runs = {"wf/a.yml": {"id": 1, "status": "in_progress"}}
    pending, failures = gate_aggregate.aggregate_verdict(
        ["wf/a.yml", "wf/b.yml"], runs, lambda i: []
    )
    assert len(pending) == 2 and failures == []


def test_all_green_run_passes():
    runs = {"wf/a.yml": {"id": 1, "status": "completed"}}
    jobs = {1: [{"name": "test", "conclusion": "success"}]}
    assert gate_aggregate.aggregate_verdict(["wf/a.yml"], runs, lambda i: jobs[i]) == (
        [],
        [],
    )


def test_own_run_judged_by_siblings_and_never_waits_for_itself():
    jobs = [
        {"name": "ci-ok", "status": "in_progress", "conclusion": None},
        {"name": "ticket", "status": "completed", "conclusion": "success"},
        {"name": "review answers", "status": "in_progress", "conclusion": None},
    ]
    pending, failures = gate_aggregate.own_verdict(jobs)
    assert len(pending) == 1 and failures == []
    jobs[2] = {"name": "review answers", "status": "completed", "conclusion": "failure"}
    pending, failures = gate_aggregate.own_verdict(jobs)
    assert pending == [] and len(failures) == 1


def test_own_workflow_path_is_static_never_listed():
    """A review-event re-run is invisible to an event=pull_request run
    listing, so the gate's own path must come from GITHUB_WORKFLOW_REF —
    a lookup through the listing misses itself and judges its own
    workflow by a sibling run the concurrency group already cancelled.
    """
    ref = "o/r/.github/workflows/ci-ok.yml@refs/pull/146/merge"
    assert gate_aggregate.own_workflow_path(ref, "o/r") == ".github/workflows/ci-ok.yml"
