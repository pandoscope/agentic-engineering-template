"""The `rerun` subcommand: stale non-green gate runs on this head SHA
are the ones re-run in place (#190).
"""

from tests.gate_support import (
    gate_rerun,
)


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
    stale = gate_rerun.stale_gate_runs(runs, ".github/workflows/ci-ok.yml")
    assert [run["id"] for run in stale] == [2, 5]


def test_a_skipped_gate_run_is_not_stale():
    """`skipped` passes required-check evaluation, so there is no red
    to supersede — re-running it would only spend Actions minutes."""
    runs = [gate_run(1, conclusion="skipped")]
    assert gate_rerun.stale_gate_runs(runs, ".github/workflows/ci-ok.yml") == []


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
    assert gate_rerun.stale_gate_runs(runs, ".github/workflows/ci-ok.yml") == []
    assert gate_rerun.gate_busy(runs, ".github/workflows/ci-ok.yml")
    assert not gate_rerun.gate_busy(
        [gate_run(1)] + runs[2:], ".github/workflows/ci-ok.yml"
    )
