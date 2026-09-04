"""The gate's multi-copy files stay pinned template-first, and the drift
check that keeps every stamped repo honest reaches its verdict.
"""

from tests.gate_support import GATE_DIR, KEYWORDS, ROOT

GATE_COPY_ROOTS = (
    ROOT / "scripts" / "ci",
    ROOT / "decision-memory" / "scripts" / "ci",
    ROOT / "evidence-memory" / "scripts" / "ci",
)


def test_gate_script_copies_are_byte_identical_template_first():
    """Every module the gate is split into, not just the entry: a copy
    that kept an old `gate_leaks.py` would run old rules under a current
    `check_gate.py` and nothing else would say so."""
    sources = sorted(GATE_DIR.glob("*.py"))
    assert [path.name for path in sources] == [
        "check_gate.py",
        "gate_aggregate.py",
        "gate_api.py",
        "gate_approval.py",
        "gate_leaks.py",
        "gate_payload.py",
        "gate_rerun.py",
        "gate_reviews.py",
        "gate_ticket.py",
    ]
    for source in sources:
        for copy_root in GATE_COPY_ROOTS:
            copy = copy_root / source.name
            assert copy.exists(), f"{copy} is missing"
            assert copy.read_bytes() == source.read_bytes(), (
                f"{copy} drifted from the template copy"
            )


def test_no_copy_carries_a_module_the_template_dropped():
    """The pin cuts both ways: a module deleted from the template but
    left behind in a copy would still be imported there."""
    expected = {path.name for path in GATE_DIR.glob("*.py")}
    for copy_root in GATE_COPY_ROOTS:
        assert {path.name for path in copy_root.glob("*.py")} == expected


def test_store_keyword_files_are_byte_identical_to_the_template():
    source = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "reference-keywords.json"
    ).read_bytes()
    for copy in (
        ROOT / ".github" / "reference-keywords.json",
        ROOT / "decision-memory" / ".github" / "reference-keywords.json",
        ROOT / "evidence-memory" / ".github" / "reference-keywords.json",
    ):
        assert copy.read_bytes() == source, f"{copy} drifted from the template copy"


def test_store_gate_workflows_are_identical_and_unticketed():
    dm = (
        ROOT / "decision-memory" / ".github" / "workflows" / "ci-ok.yml.jinja"
    ).read_text()
    em = (
        ROOT / "evidence-memory" / ".github" / "workflows" / "ci-ok.yml.jinja"
    ).read_text()
    assert dm == em
    assert "ticket" not in dm.split("jobs:")[1].split("review-answers")[0]
    template = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "ci-ok.yml.jinja"
    ).read_text()
    assert "check_gate.py ticket" in template
    # The comments carry the central file's ACTUAL values, injected at
    # render time — the rendered root copy shows the marker itself and
    # no jinja residue, so readers never chase an indirection.
    assert "reference_keywords()" in template
    root_copy = (ROOT / ".github" / "workflows" / "ci-ok.yml").read_text()
    assert KEYWORDS["no_commit_marker"] in root_copy
    assert "{%" not in root_copy and "reference_keywords" not in root_copy


def test_drift_check_fails_loudly_on_claude_md_drift():
    """Measured on pandoscope/disambiguate#82: `copier recopy` asks
    "Overwrite CLAUDE.md? (Y/n)" wherever that file diverged, and a
    non-interactive runner dies there before the diff — the drift
    check never reached its verdict. --overwrite gets past the prompt;
    the verdict then covers CLAUDE.md like every other stamped file.
    Ruled on #213: CLAUDE.md must not drift in any repo, so no restore
    exempts it (#199)."""
    text = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "lint.yml.jinja"
    ).read_text()
    recopy = text[text.index("copier recopy") : text.index("git status --porcelain")]
    assert "--overwrite" in recopy
    assert "git checkout -- CLAUDE.md" not in text


def test_drift_check_blanks_installer_owned_lock_hashes():
    """Measured on pandoscope/disambiguate#82 (#214): the post-render
    skill installer recomputes every computedHash in skills-lock.json,
    so the stamped hashes are stale by construction in every consumer.
    The verdict compares the lock with those fields blanked — skill
    set, sources and paths still fail loudly; the one installer-owned
    field does not."""
    text = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "lint.yml.jinja"
    ).read_text()
    step = text[text.index("copier recopy") : text.index("git status --porcelain")]
    assert "computedHash" in step
    assert "skills-lock.json" in step
