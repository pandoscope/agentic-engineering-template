"""Layer 3 of #189: a daily self-audit stamped into every repo.

Each repo scans ONLY itself — issue and PR descriptions and comments
plus git history — with TruffleHog's stock rules and the PUSH_BLOCKLIST
identity denylist as custom detectors. No central sweep: the job runs
where it lives, on the repo's own GITHUB_TOKEN (no org enumeration, no
app credential), free on public repos. Private repos skip cheaply at a
visibility gate — they are not exposed — with a dispatch override for
the scan-before-going-public case.

The two bash bridges are template-owned and byte-pinned like
check_gate.py: the detector generator writes the denylist values ONLY
into the config file it is told to write, and the reporter reduces
TruffleHog's JSON (whose Raw field carries the matched secret) to
value-silent ::error lines.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEMPLATE_CI = ROOT / "template" / "scripts" / "ci"
GENERATOR = "trufflehog-detectors.sh"
REPORTER = "trufflehog-report.sh"

WORKFLOW_PATHS = [
    ROOT
    / "template"
    / "{% if agentic_forge == 'github' %}.github{% endif %}"
    / "workflows"
    / "trufflehog-audit.yml.jinja",
]

PINNED_TARBALL = "trufflehog_3.97.1_linux_amd64.tar.gz"
PINNED_SHA = "f863ea3a8d786f7d097870496c977944cce7372a2fe1e56707d965016e543ece"


def run_generator(tmp_path, blocklist, conf_name="detectors.yaml"):
    conf = tmp_path / conf_name
    env = {"PATH": "/usr/bin:/bin"}
    if blocklist is not None:
        env["PUSH_BLOCKLIST"] = blocklist
    proc = subprocess.run(
        ["bash", str(TEMPLATE_CI / GENERATOR), str(conf)],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc, conf


def run_reporter(tmp_path, lines):
    findings = tmp_path / "findings.jsonl"
    findings.write_text("\n".join(lines) + "\n" if lines else "")
    return subprocess.run(
        ["bash", str(TEMPLATE_CI / REPORTER), str(findings)],
        capture_output=True,
        text=True,
    )


class TestDetectorGenerator:
    def test_labeled_entries_become_detectors_named_by_their_label(self, tmp_path):
        proc, conf = run_generator(tmp_path, "hunter2=pb:old-pw|alice@ex.com=pb:mail")
        assert proc.returncode == 0
        text = conf.read_text()
        assert "name: 'pb:old-pw'" in text
        assert "name: 'pb:mail'" in text
        assert "hunter2" in text

    def test_an_unlabeled_entry_falls_back_to_its_field_position(self, tmp_path):
        proc, conf = run_generator(tmp_path, "alpha=pb:a|bravo")
        assert proc.returncode == 0
        assert "name: 'entry 2'" in conf.read_text()

    def test_values_are_regex_escaped_into_single_quoted_yaml(self, tmp_path):
        # Double-quoted YAML rejects the regex escapes as unknown escape
        # characters and trufflehog drops the whole config — proven
        # against 3.97.1. Single quotes leave only ' special (doubled).
        proc, conf = run_generator(tmp_path, "it's+x@ex.com=pb:q")
        assert proc.returncode == 0
        text = conf.read_text()
        assert "match: 'it''s\\+x@ex\\.com'" in text
        assert '"' not in text

    def test_output_never_carries_a_value_only_labels_and_counts(self, tmp_path):
        proc, _ = run_generator(tmp_path, "sekritvalue9=pb:x")
        assert proc.returncode == 0
        assert "sekritvalue9" not in proc.stdout + proc.stderr
        assert "pb:x" in proc.stdout

    def test_trailing_newlines_and_stray_pipes_are_tolerated(self, tmp_path):
        proc, conf = run_generator(tmp_path, "alpha=pb:a||bravo=pb:b|\n")
        assert proc.returncode == 0
        text = conf.read_text()
        assert text.count("name:") == 2
        assert "name: ''" not in text

    def test_an_empty_or_unset_blocklist_writes_no_config_and_says_so(self, tmp_path):
        proc, conf = run_generator(tmp_path, None)
        assert proc.returncode == 0
        assert not conf.exists()
        assert "empty" in proc.stdout


class TestReporter:
    def test_no_findings_exits_zero_and_says_zero(self, tmp_path):
        proc = run_reporter(tmp_path, [])
        assert proc.returncode == 0
        assert "0 finding(s)" in proc.stdout

    def test_a_custom_finding_is_red_named_by_label_and_link_value_silent(
        self, tmp_path
    ):
        finding = json.dumps(
            {
                "DetectorName": "CustomRegex",
                "ExtraData": {"name": "pb:old-pw"},
                "Raw": "sekritvalue9",
                "SourceMetadata": {
                    "Data": {"Github": {"link": "https://github.com/o/r/f.txt#L3"}}
                },
            }
        )
        proc = run_reporter(tmp_path, [finding])
        assert proc.returncode == 1
        assert "pb:old-pw" in proc.stdout
        assert "https://github.com/o/r/f.txt#L3" in proc.stdout
        assert "sekritvalue9" not in proc.stdout + proc.stderr
        assert "::error" in proc.stdout

    def test_stock_findings_name_the_detector_and_noise_is_skipped(self, tmp_path):
        lines = [
            "not json at all",
            json.dumps({"level": "info", "msg": "progress line"}),
            json.dumps(
                {
                    "DetectorName": "AWS",
                    "Raw": "AKIAxxxx",
                    "SourceMetadata": {
                        "Data": {"Github": {"file": "cfg.py", "line": 9}}
                    },
                }
            ),
        ]
        proc = run_reporter(tmp_path, lines)
        assert proc.returncode == 1
        assert "AWS" in proc.stdout
        assert "cfg.py" in proc.stdout
        assert "AKIA" not in proc.stdout
        assert "1 finding(s)" in proc.stdout


def test_audit_workflow_is_stamped_daily_self_scoped_and_pinned():
    """One job per repo, responsible only for the repo it lives on
    (principal's ruling over the central org sweep): daily schedule,
    the repo's own token, the pinned scanner, both comment surfaces,
    and the denylist secret."""
    for path in WORKFLOW_PATHS:
        text = path.read_text()
        assert "schedule:" in text, path
        assert text.count("* * *") == 1 and "* * 1" not in text, path  # daily
        assert "workflow_dispatch:" in text, path
        assert PINNED_TARBALL in text, path
        assert PINNED_SHA in text, path
        assert "--issue-comments" in text, path
        assert "--pr-comments" in text, path
        assert "secrets.PUSH_BLOCKLIST" in text, path
        assert "runs-on: {{ agentic_actions_runner }}" in text, path
        assert "trufflehog-detectors.sh" in text, path
        assert "trufflehog-report.sh" in text, path
        assert "GITHUB_REPOSITORY" in text, path


def test_private_repos_skip_at_a_visibility_gate_with_an_override():
    """Private repos are not exposed, so their daily job stops at a
    cheap visibility check — but a dispatch override exists for the
    scan-this-before-making-it-public case."""
    text = WORKFLOW_PATHS[0].read_text()
    assert "scan_private" in text
    assert ".private" in text


def test_audit_scripts_are_byte_identical_everywhere():
    for name in (GENERATOR, REPORTER):
        source = (TEMPLATE_CI / name).read_bytes()
        for copy in (
            ROOT / "scripts" / "ci" / name,
            ROOT / "decision-memory" / "scripts" / "ci" / name,
            ROOT / "evidence-memory" / "scripts" / "ci" / name,
        ):
            assert copy.read_bytes() == source, f"{copy} drifted from the template"


def test_root_stamp_carries_the_audit_workflow():
    text = (ROOT / ".github" / "workflows" / "trufflehog-audit.yml").read_text()
    assert "{%" not in text
    assert "runs-on: ubuntu-latest" in text
