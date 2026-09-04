"""Layer 4 of #189: the forge-native net, independent of our tooling.

#104 asked for `run_secret_scanning` as a second, independent check.
That content-scan API is gated on GitHub Advanced Security — verified
empirically: every public repo in the org answers "Repository does not
have GitHub Advanced Security enabled" — so the free forge-native net
a public repo does get is wired instead: GitHub's own secret scanning
(alerts) and push protection. A daily per-repo job (the layer-3 shape)
fails when either engine is switched off, or when any secret-scanning
alert is open. Alerts carry the secret itself in their `secret` field,
so the reporter is value-silent like trufflehog-report.sh.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_CI = ROOT / "template" / "scripts" / "ci"
REPORTER = "secret-scanning-report.sh"
WORKFLOW = (
    ROOT
    / "template"
    / "{% if agentic_forge == 'github' %}.github{% endif %}"
    / "workflows"
    / "secret-scanning.yml.jinja"
)


def run_reporter(tmp_path, text):
    alerts = tmp_path / "alerts.json"
    alerts.write_text(text)
    return subprocess.run(
        ["bash", str(TEMPLATE_CI / REPORTER), str(alerts)],
        capture_output=True,
        text=True,
    )


def alert(number, **extra):
    d = {
        "number": number,
        "state": "open",
        "secret_type": "aws_access_key_id",
        "secret_type_display_name": "Amazon AWS Access Key ID",
        "secret": "AKIAsekritvalue9",
        "html_url": f"https://github.com/o/r/security/secret-scanning/{number}",
        "validity": "unknown",
    }
    d.update(extra)
    return d


class TestReporter:
    def test_no_alerts_exits_zero_and_says_zero(self, tmp_path):
        proc = run_reporter(tmp_path, "[]\n")
        assert proc.returncode == 0
        assert "0 open alert(s)" in proc.stdout

    def test_an_empty_file_counts_as_no_alerts(self, tmp_path):
        proc = run_reporter(tmp_path, "")
        assert proc.returncode == 0
        assert "0 open alert(s)" in proc.stdout

    def test_an_open_alert_is_red_named_by_type_and_link_value_silent(self, tmp_path):
        proc = run_reporter(tmp_path, json.dumps([alert(7)]))
        assert proc.returncode == 1
        assert "::error" in proc.stdout
        assert "Amazon AWS Access Key ID" in proc.stdout
        assert "https://github.com/o/r/security/secret-scanning/7" in proc.stdout
        assert "sekrit" not in proc.stdout + proc.stderr
        assert "1 open alert(s)" in proc.stdout

    def test_paginated_output_is_read_as_concatenated_pages(self, tmp_path):
        # `gh api --paginate` on a list endpoint emits one JSON array per
        # page back-to-back; every page counts.
        pages = json.dumps([alert(1)]) + "\n" + json.dumps([alert(2), alert(3)])
        proc = run_reporter(tmp_path, pages)
        assert proc.returncode == 1
        assert "3 open alert(s)" in proc.stdout

    def test_a_missing_display_name_falls_back_to_the_type(self, tmp_path):
        a = alert(4)
        del a["secret_type_display_name"]
        proc = run_reporter(tmp_path, json.dumps([a]))
        assert proc.returncode == 1
        assert "aws_access_key_id" in proc.stdout


def test_native_scan_workflow_is_daily_self_scoped_and_checks_both_engines():
    """Same shape as the layer-3 audit: one job per repo, the repo's
    own token, daily, private repos skipped at the visibility gate.
    It asserts the forge's own engines are ON (a public repo with the
    free net switched off is itself the finding) and reads open alerts
    with the least permission that can."""
    text = WORKFLOW.read_text()
    assert "schedule:" in text
    assert text.count("* * *") == 1 and "* * 1" not in text  # daily
    assert "workflow_dispatch:" in text
    assert "scan_private" in text
    assert ".private" in text
    assert "security-events: read" in text
    assert "runs-on: {{ agentic_actions_runner }}" in text
    assert "secret_scanning.status" in text
    assert "secret_scanning_push_protection.status" in text
    assert "secret-scanning/alerts" in text
    assert "state=open" in text
    assert "--paginate" in text
    assert REPORTER in text
    assert "GITHUB_REPOSITORY" in text
    # The alert payload carries the secret: it goes to a file, never the log.
    assert "RUNNER_TEMP" in text


def test_an_unobservable_engine_status_is_a_notice_not_a_failure():
    """Measured on the first run: the repo token gets no
    security_and_analysis block at all (admin scope), so 'unset' must
    not fail every public repo daily — it says so and lets the alert
    feed be the check. A status the token CAN see and that is not
    'enabled' stays red."""
    text = WORKFLOW.read_text()
    assert '"unset"' in text
    assert "::notice::" in text
    assert "not visible to the repo token" in text
    assert '"$status" != "enabled"' in text


def test_native_scan_workflow_does_not_collide_with_the_audit_schedule():
    audit = WORKFLOW.with_name("trufflehog-audit.yml.jinja").read_text()

    def cron(text):
        return next(line for line in text.splitlines() if "cron:" in line).strip()

    assert cron(audit) != cron(WORKFLOW.read_text())


def test_reporter_is_byte_identical_everywhere():
    source = (TEMPLATE_CI / REPORTER).read_bytes()
    for copy in (
        ROOT / "scripts" / "ci" / REPORTER,
        ROOT / "decision-memory" / "scripts" / "ci" / REPORTER,
        ROOT / "evidence-memory" / "scripts" / "ci" / REPORTER,
    ):
        assert copy.read_bytes() == source, f"{copy} drifted from the template"


def test_root_stamp_carries_the_native_scan_workflow():
    text = (ROOT / ".github" / "workflows" / "secret-scanning.yml").read_text()
    assert "{%" not in text
    assert "runs-on: ubuntu-latest" in text
