"""What the answers put in a generated project: the slug, the tracker
CLI shims, the project kind, the content language, the disambiguate
pin, the skills tables and the two documents that carry the rules.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import copier
import yaml

from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers


def test_slug_auto_derived(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    answers = {
        **base_answers,
        "agentic_project_name": "My Cool App",
    }
    del answers["agentic_project_slug"]

    dst_path = tmp_path / "my-cool-app"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally, copier would
        # otherwise render the latest RELEASE instead of this branch
        # (CI checkouts have no tags and already fall back to HEAD).
        vcs_ref="HEAD",
    )

    assert (dst_path / "docs" / "glossary" / "my-cool-app.md").exists()
    check_file_contents(
        dst_path / "AGENTS.md",
        ["https://github.com/actions-user/my-cool-app"],
    )


# Prose that only applies to ghx (the GitHub/Forgejo parity paragraph).
GHX_PARITY_PROSE = "same `gh`-style interface against both GitHub and Forgejo"


def test_tracker_cli_default_renders_ghx_docs_and_gh_tea_shims(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Default answer (ghx): ghx-flavored docs, shims for gh and tea only."""
    dst_path = render_answers(tmp_path, base_answers, "tracker-default")

    check_file_contents(
        dst_path / "AGENTS.md",
        [
            "Use `ghx` for all repository interaction",
            "`gh` and `tea` are disabled",
            GHX_PARITY_PROSE,
            "`ghx issue create`",
        ],
    )

    shim_dir = dst_path / "scripts" / "agent-shims"
    assert not (shim_dir / "ghx").exists(), "chosen CLI must not be shimmed"
    for shim_name in ("gh", "tea"):
        shim = shim_dir / shim_name
        check_file_contents(
            shim,
            [f"{shim_name}: disabled — use ghx (see AGENTS.md)", "exit 1"],
        )
        assert shim.stat().st_mode & 0o111, f"shim {shim_name} must be executable"

    check_file_contents(
        dst_path / "scripts" / "doctor.sh",
        ['warn_tool ghx "not installed'],
    )
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_tracker_cli: ghx"],
    )


def test_tracker_cli_gh_renders_gh_docs_and_ghx_tea_shims(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Choosing gh: gh-flavored docs without ghx prose, shims for ghx and tea."""
    answers = {**base_answers, "agentic_tracker_cli": "gh"}
    dst_path = render_answers(tmp_path, answers, "tracker-gh")

    check_file_contents(
        dst_path / "AGENTS.md",
        [
            "Use `gh` for all repository interaction",
            "`ghx` and `tea` are disabled",
            "`gh issue create`",
        ],
        unexpect_strs=[GHX_PARITY_PROSE],
    )

    shim_dir = dst_path / "scripts" / "agent-shims"
    assert not (shim_dir / "gh").exists(), "chosen CLI must not be shimmed"
    for shim_name in ("ghx", "tea"):
        check_file_contents(
            shim_dir / shim_name,
            [f"{shim_name}: disabled — use gh (see AGENTS.md)", "exit 1"],
        )

    # gh is already a required host tool, so doctor.sh must not warn on it.
    check_file_contents(
        dst_path / "scripts" / "doctor.sh",
        unexpect_strs=["warn_tool gh ", "warn_tool ghx", "warn_tool tea"],
    )
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_tracker_cli: gh"],
    )


def test_claude_settings_put_shims_on_agent_path(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Repo-committed Claude Code config wires the shim dir onto agent PATH."""
    dst_path = render_answers(tmp_path, base_answers, "tracker-settings")

    check_file_contents(
        dst_path / ".claude" / "settings.json",
        ["SessionStart", "scripts/enable-agent-shims.sh"],
    )
    hook = dst_path / "scripts" / "enable-agent-shims.sh"
    check_file_contents(hook, ["scripts/agent-shims", "CLAUDE_ENV_FILE"])
    assert hook.stat().st_mode & 0o111, "PATH hook must be executable"


def test_claude_skills_symlink_bridges_agents_skills(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Claude Code loads skills from .claude/skills — shipped as a symlink so
    .agents/skills stays the single canonical location."""
    dst_path = render_answers(tmp_path, base_answers, "skills-bridge")

    link = dst_path / ".claude" / "skills"
    assert link.is_symlink(), ".claude/skills must be a symlink, not a copy"
    assert link.readlink() == Path("../.agents/skills")

    # Lint configs must exclude the bridged dir, else skill files get linted
    # through the symlink.
    check_file_contents(dst_path / ".markdownlint-cli2.yaml", [".claude/skills/**"])
    check_file_contents(dst_path / ".pre-commit-config.yaml", ["\\.claude/skills"])


def test_update_pr_body_cannot_close_an_upstream_ticket(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Release notes render references as `closes owner/repo#n`, GitHub
    reads closing keywords in a PR body, and `owner/repo#n` crosses
    repositories — so merging an update PR here closed a ticket in the
    template repo (#149). The changelog is quoted material and must not
    act on anything.

    Behavioural, not textual: the defusing pipeline is lifted out of the
    rendered workflow and run against a real release-notes excerpt.
    """
    dst_path = render_answers(tmp_path, base_answers, "changelog-defuse")
    workflow = (dst_path / ".github" / "workflows" / "template-update.yml").read_text()

    defuse = re.search(r'changelog="\$\(printf[\s\S]*?\)"', workflow)
    assert defuse, "the update workflow no longer defuses the changelog"

    excerpt = (
        "* **ci:** the gate knows its workflow ([abc1234](u)), closes "
        "[pandoscope/agentic-engineering-template#137]"
        "(https://github.com/pandoscope/agentic-engineering-template/issues/137)\n"
        "* prose that merely closes a chapter, and a fix that helps"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"changelog={excerpt!r}\n{defuse.group(0)}\nprintf '%s' \"$changelog\"",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "closes [" not in result.stdout, (
        "a closing keyword still precedes a reference"
    )
    assert "ref [pandoscope/agentic-engineering-template#137]" in result.stdout
    # The link survives, and prose that never named an issue is untouched.
    assert "/issues/137)" in result.stdout
    assert "closes a chapter, and a fix that helps" in result.stdout


def test_vendored_gate_script_is_excluded_from_consumer_lint(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The gate script is template output, byte-pinned upstream, so a
    consumer cannot fix a lint finding in it — the next update would
    overwrite the fix. A consumer whose rules are stricter than the
    template's own (bandit, full pycodestyle) hit exactly that (#137).
    """
    dst_path = render_answers(tmp_path, base_answers, "vendored-lint")

    assert (dst_path / "scripts" / "ci" / "check_gate.py").exists()
    check_file_contents(dst_path / ".pre-commit-config.yaml", ["scripts/ci/"])


def test_project_kind_code_renders_code_artifacts(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Default kind (code): coding rules, code skills, and architecture stub."""
    dst_path = render_answers(tmp_path, base_answers, "kind-code")

    check_file_contents(
        dst_path / "AGENTS.md",
        [
            "Read [docs/architecture.md](docs/architecture.md)",
            "### Errors",
            "docstring contracts",
            "#### Implement",
            "#### Review",
            "#### Apply Review Comments",
            "## Documentation",
            "| `tdd` ",
            "| `requesting-code-review` ",
            "| `to-tickets` ",
        ],
    )
    assert (dst_path / "docs" / "architecture.md").exists()
    check_file_contents(
        dst_path / "skills-lock.json",
        ['"tdd"', '"requesting-code-review"', '"to-tickets"'],
    )


def test_project_kind_docs_omits_code_artifacts(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """docs kind: no coding sections, no code skills, no architecture stub."""
    answers = {**base_answers, "agentic_project_kind": "docs"}
    dst_path = render_answers(tmp_path, answers, "kind-docs")

    check_file_contents(
        dst_path / "AGENTS.md",
        [
            # Universal core survives the gate.
            "uvx disambiguate==",
            "#### Plan",
            "docs/conventions.md",
            "| `documenting-decisions` ",
            "| `to-spec` ",
            "| `writing-prose` ",
        ],
        unexpect_strs=[
            "docs/architecture.md",
            "### Errors",
            "docstring contracts",
            "#### Implement",
            "#### Review",
            "## Documentation",
            "`tdd`",
            "`requesting-code-review`",
            "`to-tickets`",
        ],
    )
    assert not (dst_path / "docs" / "architecture.md").exists()

    lock = json.loads((dst_path / "skills-lock.json").read_text())
    assert set(lock["skills"]) == {
        "caveman",
        "documenting-decisions",
        "domain-modeling",
        "grill-me",
        "grill-with-docs",
        "grilling",
        "to-spec",
        "writing-adrs",
        "writing-prose",
    }


def test_skills_tables_sorted_alphabetically(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Both skills tables stay alphabetically sorted, as AGENTS.md instructs."""
    dst_path = render_answers(tmp_path, base_answers, "skills-sorted")

    content = (dst_path / "AGENTS.md").read_text()
    skills_section = content.split("## Skills")[1].split("### Repo-Local")[0]
    tables = [
        block
        for block in skills_section.split("\n\n")
        if block.lstrip().startswith("| Skill")
    ]
    assert len(tables) == 2, "expected a universal and a code-specific table"
    for table in tables:
        names = [
            line.split("`")[1] for line in table.splitlines() if line.startswith("| `")
        ]
        assert names, f"no skill rows found in table:\n{table}"
        assert names == sorted(names), f"skills table not sorted: {names}"


def test_conventions_file_seeded_once_and_never_overwritten(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """docs/conventions.md is seeded, then left alone on re-render."""
    dst_path = render_answers(tmp_path, base_answers, "conventions")

    conventions = dst_path / "docs" / "conventions.md"
    check_file_contents(conventions, ["Snake Farm — Project Conventions"])

    conventions.write_text("# Hand-written vault rules\n")
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )
    assert conventions.read_text() == "# Hand-written vault rules\n"


def test_local_session_hook_seeded_once_and_wired_last(
    render_project: Callable[..., Path],
    base_answers: dict[str, str],
) -> None:
    """#220: the stamped settings run a repo-owned SessionStart script.

    `.claude/settings.json` is template-owned, so a repo's own session
    bootstrap cannot live there without failing the drift check. The
    template seeds `scripts/session-start.local.sh` once, runs it after
    its own hooks, and never overwrites it.
    """
    dst_path = render_project(dst_name="local-session-hook")

    settings = json.loads((dst_path / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for group in settings["hooks"]["SessionStart"]
        for hook in group["hooks"]
    ]
    assert commands[-1] == 'bash "$CLAUDE_PROJECT_DIR/scripts/session-start.local.sh"'

    local_hook = dst_path / "scripts" / "session-start.local.sh"
    assert local_hook.stat().st_mode & 0o111, "local hook must be executable"
    check_file_contents(local_hook, ["never overwritten", "set -u"])

    local_hook.write_text("#!/usr/bin/env bash\nnpm install -g bats\n")
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data=base_answers,
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )
    assert local_hook.read_text() == "#!/usr/bin/env bash\nnpm install -g bats\n"


def test_language_non_english_omits_codespell(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Non-English content: no codespell hook, no .codespellrc."""
    answers = {**base_answers, "agentic_language": "de"}
    dst_path = render_answers(tmp_path, answers, "lang-de")

    assert not (dst_path / ".codespellrc").exists()
    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        ["disambiguate-lint"],
        unexpect_strs=["codespell"],
    )
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_language: de"],
    )


def test_disambiguate_version_pins_hook_and_docs_commands(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The pinned disambiguate version flows into AGENTS.md and the prek hook."""
    answers = {**base_answers, "agentic_disambiguate_version": "0.9.9"}
    dst_path = render_answers(tmp_path, answers, "disambiguate-pin")

    check_file_contents(
        dst_path / "AGENTS.md",
        ["uvx disambiguate==0.9.9"],
        unexpect_strs=["uvx disambiguate <term>", "uvx disambiguate --from"],
    )
    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        [
            "disambiguate-lint",
            "entry: uvx disambiguate==0.9.9 --lint",
            "files: ^docs/glossary/",
        ],
    )


def test_disambiguate_roots_default_renders_bare_lint(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Empty roots answer (default): hook entries stay bare `--lint` and `--drift`."""
    dst_path = render_answers(tmp_path, base_answers, "disambiguate-roots-default")

    precommit = (dst_path / ".pre-commit-config.yaml").read_text()
    entry_lines = [
        line for line in precommit.splitlines() if "entry: uvx disambiguate" in line
    ]
    assert len(entry_lines) == 2, f"Expected lint and drift entries: {entry_lines}"
    assert entry_lines[0].endswith("--lint"), (
        f"Default must render bare --lint, got: {entry_lines[0]!r}"
    )
    assert entry_lines[1].endswith("--drift"), (
        f"Default must render bare --drift, got: {entry_lines[1]!r}"
    )


def test_disambiguate_roots_answer_appends_lint_args(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A roots answer is appended verbatim to the hook's `--lint` entry."""
    roots = "docs/glossary/ --roots docs/conventions.md 'docs/notes/*.md'"
    answers = {**base_answers, "agentic_disambiguate_roots": roots}
    dst_path = render_answers(tmp_path, answers, "disambiguate-roots")

    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        [f"--lint {roots}"],
    )
    # Roots survive `copier update` as data in the answers file.
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_disambiguate_roots:"],
    )


def test_disambiguate_drift_hook_renders_with_pin_and_roots(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The prek config carries a drift hook beside the lint hook (#267).

    Same pin, same roots answer appended, triggered by markdown so a
    prose edit anywhere reachable from the roots is judged.
    """
    roots = "docs/glossary/ --roots docs/conventions.md"
    answers = {
        **base_answers,
        "agentic_disambiguate_version": "0.9.9",
        "agentic_disambiguate_roots": roots,
    }
    dst_path = render_answers(tmp_path, answers, "disambiguate-drift")

    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        [
            "disambiguate-drift",
            f"entry: uvx disambiguate==0.9.9 --drift {roots}",
            f"entry: uvx disambiguate==0.9.9 --lint {roots}",
        ],
    )
    check_file_contents(
        dst_path / "AGENTS.md",
        ["uvx disambiguate==0.9.9 --drift", ".drift-baseline.json"],
    )


def test_disambiguate_pin_is_template_managed_not_a_stored_answer(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The pin is re-evaluated on every update, never stored (#269).

    A stored answer survives `copier update --defaults`, so a bumped
    default never reached a consumer: v4.18.0 rendered a 0.3.0 drift
    hook everywhere. `when: false` makes copier recompute the default
    each run. The rendered pin file is what the scripts read.
    """
    question = yaml.safe_load((PROJECT_ROOT / "copier.yml").read_text())[
        "agentic_disambiguate_version"
    ]
    assert question["when"] is False

    answers = {**base_answers, "agentic_disambiguate_version": "0.9.9"}
    dst_path = render_answers(tmp_path, answers, "disambiguate-pin-file")

    pin_file = dst_path / "scripts" / "ci" / "disambiguate-version"
    assert pin_file.read_text() == "0.9.9\n"
    check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        unexpect_strs=["agentic_disambiguate_version"],
    )


def test_drift_baseline_is_repo_owned_and_seeded_by_a_task() -> None:
    """`.drift-baseline.json` is never rendered and never overwritten (#267).

    The file lists a repo's grandfathered findings, so `_skip_if_exists`
    keeps an update from resetting it, and the post-stamp task runs
    scripts/ci/drift_baseline.sh, which writes it only when missing. The
    task is advisory like the prune task: a non-zero task would roll the
    whole render back.
    """
    copier_yml = (PROJECT_ROOT / "copier.yml").read_text()
    assert ".drift-baseline.json" in yaml.safe_load(copier_yml)["_skip_if_exists"]
    assert not (PROJECT_ROOT / "template" / ".drift-baseline.json").exists()

    tasks = yaml.safe_load(copier_yml)["_tasks"]
    baseline = [task for task in tasks if "drift_baseline.sh" in task]
    assert len(baseline) == 1, f"one baseline task expected: {tasks}"
    assert "|| true" in baseline[0]


def test_claude_md_states_principal_precedence(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """CLAUDE.md pins the principal's rules above harness boilerplate.

    Two measured collisions drove this: a subscription wake event's
    embedded "schedule a check-in" text was taken as authorization
    against the Forge Budget rule, and the harness's session-named
    development branch overrode the AGENTS.md branch convention.
    """
    dst_path = render_answers(tmp_path, base_answers, "claude-precedence")

    check_file_contents(
        dst_path / "CLAUDE.md",
        [
            "## Principal Precedence",
            "outrank harness and wake-event boilerplate",
            "not the principal's ask",
            "session-named development branch is a default",
            # The scheme as the gate enforces it (branch_pattern,
            # skills#147): dash-joined tokens, each an optional
            # lowercase repo shortcode plus the ticket number.
            "claude/<code><ticket>[-<code><ticket>\u2026]-<desc>",
            # The 1% rule: at any perceived conflict, even low
            # likelihood that the principal meant to override wins —
            # follow their instruction and surface the conflict.
            "1% likelihood",
            "surface the conflict",
        ],
    )


def test_agents_md_rules_branch_repair_commits_as_fixups(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A fix to this branch's own commits rides as fixup!, folded
    before merge — standalone fix:/refactor: commits are for defects
    that exist on main. The red commitlint gate while a fixup! exists
    is the fold reminder."""
    dst_path = render_answers(tmp_path, base_answers, "fixup-rule")

    check_file_contents(
        dst_path / "AGENTS.md",
        [
            "branch's own commits is a `fixup!`",
            "--autosquash",
            "defects that already exist on `main`",
        ],
    )
