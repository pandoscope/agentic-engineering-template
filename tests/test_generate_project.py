from __future__ import annotations

from collections.abc import Sequence
import json
import re
import subprocess
from pathlib import Path

import copier
import pytest
import yaml

from tests.conftest import load_module

PROJECT_ROOT = Path(__file__).parent.parent


def _check_file_contents(
    file_path: Path,
    expected_strs: Sequence[str] = (),
    unexpect_strs: Sequence[str] = (),
) -> None:
    assert file_path.exists(), f"Expected file missing: {file_path}"
    file_content = file_path.read_text()
    for content in expected_strs:
        assert content in file_content, f"Expected {content!r} in {file_path}"
    for content in unexpect_strs:
        assert content not in file_content, f"Unexpected {content!r} in {file_path}"


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
    _check_file_contents(
        dst_path / "AGENTS.md",
        ["https://github.com/actions-user/my-cool-app"],
    )


# Prose that only applies to ghx (the GitHub/Forgejo parity paragraph).
GHX_PARITY_PROSE = "same `gh`-style interface against both GitHub and Forgejo"


def _render(tmp_path: Path, answers: dict[str, str], dst_name: str) -> Path:
    dst_path = tmp_path / dst_name
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
    return dst_path


def test_tracker_cli_default_renders_ghx_docs_and_gh_tea_shims(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Default answer (ghx): ghx-flavored docs, shims for gh and tea only."""
    dst_path = _render(tmp_path, base_answers, "tracker-default")

    _check_file_contents(
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
        _check_file_contents(
            shim,
            [f"{shim_name}: disabled — use ghx (see AGENTS.md)", "exit 1"],
        )
        assert shim.stat().st_mode & 0o111, f"shim {shim_name} must be executable"

    _check_file_contents(
        dst_path / "scripts" / "doctor.sh",
        ['warn_tool ghx "not installed'],
    )
    _check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_tracker_cli: ghx"],
    )


def test_tracker_cli_gh_renders_gh_docs_and_ghx_tea_shims(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Choosing gh: gh-flavored docs without ghx prose, shims for ghx and tea."""
    answers = {**base_answers, "agentic_tracker_cli": "gh"}
    dst_path = _render(tmp_path, answers, "tracker-gh")

    _check_file_contents(
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
        _check_file_contents(
            shim_dir / shim_name,
            [f"{shim_name}: disabled — use gh (see AGENTS.md)", "exit 1"],
        )

    # gh is already a required host tool, so doctor.sh must not warn on it.
    _check_file_contents(
        dst_path / "scripts" / "doctor.sh",
        unexpect_strs=["warn_tool gh ", "warn_tool ghx", "warn_tool tea"],
    )
    _check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_tracker_cli: gh"],
    )


def test_claude_settings_put_shims_on_agent_path(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Repo-committed Claude Code config wires the shim dir onto agent PATH."""
    dst_path = _render(tmp_path, base_answers, "tracker-settings")

    _check_file_contents(
        dst_path / ".claude" / "settings.json",
        ["SessionStart", "scripts/enable-agent-shims.sh"],
    )
    hook = dst_path / "scripts" / "enable-agent-shims.sh"
    _check_file_contents(hook, ["scripts/agent-shims", "CLAUDE_ENV_FILE"])
    assert hook.stat().st_mode & 0o111, "PATH hook must be executable"


def test_claude_skills_symlink_bridges_agents_skills(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Claude Code loads skills from .claude/skills — shipped as a symlink so
    .agents/skills stays the single canonical location."""
    dst_path = _render(tmp_path, base_answers, "skills-bridge")

    link = dst_path / ".claude" / "skills"
    assert link.is_symlink(), ".claude/skills must be a symlink, not a copy"
    assert link.readlink() == Path("../.agents/skills")

    # Lint configs must exclude the bridged dir, else skill files get linted
    # through the symlink.
    _check_file_contents(dst_path / ".markdownlint-cli2.yaml", [".claude/skills/**"])
    _check_file_contents(dst_path / ".pre-commit-config.yaml", ["\\.claude/skills"])


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
    dst_path = _render(tmp_path, base_answers, "changelog-defuse")
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
    dst_path = _render(tmp_path, base_answers, "vendored-lint")

    assert (dst_path / "scripts" / "ci" / "check_gate.py").exists()
    _check_file_contents(dst_path / ".pre-commit-config.yaml", ["scripts/ci/"])


def test_project_kind_code_renders_code_artifacts(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Default kind (code): coding rules, code skills, and architecture stub."""
    dst_path = _render(tmp_path, base_answers, "kind-code")

    _check_file_contents(
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
    _check_file_contents(
        dst_path / "skills-lock.json",
        ['"tdd"', '"requesting-code-review"', '"to-tickets"'],
    )


def test_project_kind_docs_omits_code_artifacts(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """docs kind: no coding sections, no code skills, no architecture stub."""
    answers = {**base_answers, "agentic_project_kind": "docs"}
    dst_path = _render(tmp_path, answers, "kind-docs")

    _check_file_contents(
        dst_path / "AGENTS.md",
        [
            # Universal core survives the gate.
            "uvx disambiguate==",
            "#### Plan",
            "docs/conventions.md",
            "| `documenting-decisions` ",
            "| `to-spec` ",
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
    }


def test_skills_tables_sorted_alphabetically(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Both skills tables stay alphabetically sorted, as AGENTS.md instructs."""
    dst_path = _render(tmp_path, base_answers, "skills-sorted")

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
    dst_path = _render(tmp_path, base_answers, "conventions")

    conventions = dst_path / "docs" / "conventions.md"
    _check_file_contents(conventions, ["Snake Farm — Project Conventions"])

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


def test_language_non_english_omits_codespell(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Non-English content: no codespell hook, no .codespellrc."""
    answers = {**base_answers, "agentic_language": "de"}
    dst_path = _render(tmp_path, answers, "lang-de")

    assert not (dst_path / ".codespellrc").exists()
    _check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        ["disambiguate-lint"],
        unexpect_strs=["codespell"],
    )
    _check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_language: de"],
    )


def test_disambiguate_version_pins_hook_and_docs_commands(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The pinned disambiguate version flows into AGENTS.md and the prek hook."""
    answers = {**base_answers, "agentic_disambiguate_version": "0.9.9"}
    dst_path = _render(tmp_path, answers, "disambiguate-pin")

    _check_file_contents(
        dst_path / "AGENTS.md",
        ["uvx disambiguate==0.9.9"],
        unexpect_strs=["uvx disambiguate <term>", "uvx disambiguate --from"],
    )
    _check_file_contents(
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
    """Empty roots answer (default): hook entry stays bare `--lint`."""
    dst_path = _render(tmp_path, base_answers, "disambiguate-roots-default")

    precommit = (dst_path / ".pre-commit-config.yaml").read_text()
    entry_lines = [
        line for line in precommit.splitlines() if "entry: uvx disambiguate" in line
    ]
    assert len(entry_lines) == 1, f"Expected one disambiguate entry: {entry_lines}"
    assert entry_lines[0].endswith("--lint"), (
        f"Default must render bare --lint, got: {entry_lines[0]!r}"
    )


def test_disambiguate_roots_answer_appends_lint_args(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A roots answer is appended verbatim to the hook's `--lint` entry."""
    roots = "docs/glossary/ --roots docs/conventions.md 'docs/notes/*.md'"
    answers = {**base_answers, "agentic_disambiguate_roots": roots}
    dst_path = _render(tmp_path, answers, "disambiguate-roots")

    _check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        [f"--lint {roots}"],
    )
    # Roots survive `copier update` as data in the answers file.
    _check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        ["agentic_disambiguate_roots:"],
    )


def test_github_forge_ships_template_update_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub forge: scheduled copier-update workflow rendered verbatim."""
    dst_path = _render(tmp_path, base_answers, "updater-github")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "template-update.yml",
        [
            "workflow_dispatch",
            "copier update",
            "--defaults --trust --skip-tasks",
            "copier-template-extensions",
            "actions/create-github-app-token",
            "RELEASE_BOT_CLIENT_ID",
            "RELEASE_BOT_PRIVATE_KEY",
            "chore/template-update-",
            # GitHub expressions must survive rendering (file is not Jinja).
            "${{ steps.app-token.outputs.token }}",
            # The ticket gate's designed escape reaches the PR that needs
            # it: the label is bootstrapped (this PR may be delivering
            # labels.toml's entry) and applied at creation.
            "gh label create automated",
            "--label automated",
            # A stale update PR is joined, never closed: the new release
            # lands as a commit on it and the PR is refreshed in place,
            # so conflict resolutions on the branch survive (#137).
            "Skip on a current update PR, join a stale one",
            'gh pr edit "$EXISTING_PR"',
        ],
    )


def test_forgejo_forge_ships_no_github_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Forgejo forge: no .github directory — the updater is GitHub-only."""
    answers = {
        **base_answers,
        "agentic_forge": "forgejo",
        "agentic_forgejo_host": "git.example.com",
    }
    dst_path = _render(tmp_path, answers, "updater-forgejo")

    assert not (dst_path / ".github").exists()


def test_github_forge_ships_notify_sessions_manifest_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub forge: the sessions-marker notify workflow is vendored.

    Vendored unconditionally because its path filter makes it inert in
    any repo without a `.pandoscope-sessions` marker; the marker itself
    stays a per-repo opt-in the template never stamps.
    """
    dst_path = _render(tmp_path, base_answers, "notify-github")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "notify-sessions-manifest.yml",
        [
            'paths: [".pandoscope-sessions"]',
            "event_type=sessions-marker-changed",
            "runs-on: ubuntu-latest",
            # GitHub expressions must survive rendering (file is not Jinja).
            "${{ vars.RELEASE_BOT_CLIENT_ID }}",
            "${{ secrets.RELEASE_BOT_PRIVATE_KEY }}",
        ],
    )
    assert not (dst_path / ".pandoscope-sessions").exists(), (
        "the marker is a per-repo opt-in, never stamped by the template"
    )


def test_lint_workflow_guards_vendored_template_drift(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Hand-editing a template-vendored file must turn CI red.

    The lint workflow recopies the STAMPED template version (the
    `_commit` in the answers file) and fails on any diff — a
    local-modification check, orthogonal to the being-behind drift the
    template-update weekly audit covers. Rendered even without prek,
    like the conflict-marker job: both guard the template contract. The
    template repo itself carries no answers file, so the job skips
    cleanly there.
    """
    for precommit in ("prek", "none"):
        answers = {**base_answers, "agentic_precommit": precommit}
        dst_path = _render(tmp_path, answers, f"lint-drift-{precommit}")

        _check_file_contents(
            dst_path / ".github" / "workflows" / "lint.yml",
            [
                "copier recopy",
                '--defaults --trust --skip-tasks --vcs-ref "$stamped"',
                "copier-template-extensions",
                "awk '$1 == \"_commit:\" {print $2}'",
                "not a stamped repo",
                # CLAUDE.md invites local Learnings and copier update
                # three-way-merges them — the drift job must restore
                # it after recopy, or every Learnings-bearing repo
                # goes permanently red.
                "git checkout -- CLAUDE.md",
                "git status --porcelain",
                "template-owned",
            ],
        )


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
    dst_path = _render(tmp_path, base_answers, "claude-precedence")

    _check_file_contents(
        dst_path / "CLAUDE.md",
        [
            "## Principal Precedence",
            "outrank harness and wake-event boilerplate",
            "not the principal's ask",
            "session-named development branch is a default",
            # The 1% rule: at any perceived conflict, even low
            # likelihood that the principal meant to override wins —
            # follow their instruction and surface the conflict.
            "1% likelihood",
            "surface the conflict",
        ],
    )


def test_commitlint_config_rejects_fixup_and_squash_commits(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """commitlint must parse fixup!/squash! headers, not skip them.

    commitlint's defaultIgnores silently exempts fixup!/squash!
    commits (measured: a fixup! commit sailed through the PR gate), so
    "the type enum IS the allow-list" only holds with defaultIgnores
    off. Merge headers get NO exemption — merge commits are allowed
    only on main (ruled), and this gate lints only feature-branch
    commits, so a Merge header in its range is itself the violation.
    Only git-generated revert headers stay exempt: they are not
    conventional, and reverts are legitimate anywhere.
    """
    dst_path = _render(tmp_path, base_answers, "commitlint-ignores")

    _check_file_contents(
        dst_path / "commitlint.config.mjs",
        [
            "defaultIgnores: false",
            "startsWith('Revert \"')",
        ],
        unexpect_strs=['startsWith("Merge'],
    )


def test_grilling_pinned_to_frankify_derivation(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """`grilling` pins the frankify-app/skills derivation, not upstream."""
    dst_path = _render(tmp_path, base_answers, "grilling-pin")

    lock = json.loads((dst_path / "skills-lock.json").read_text())
    grilling = lock["skills"]["grilling"]
    assert grilling["source"] == "frankify-app/skills"
    assert grilling["skillPath"] == "derived/grilling/SKILL.md"


def test_decision_memory_url_env_var_contract(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """DECISION_MEMORY_URL is an env-var-only contract: no copier question,
    no value in any committed artifact (.copier-answers* included); AGENTS.md
    documents the contract and doctor.sh checks the env var."""
    # Even if a consumer passes a URL as copier data, it must render nowhere.
    stray_url = "https://github.com/acme/decision-memory"
    answers = {**base_answers, "agentic_decision_memory_url": stray_url}
    dst_path = _render(tmp_path, answers, "decision-memory")

    for path in dst_path.rglob("*"):
        if path.is_file():
            assert stray_url not in path.read_text(), (
                f"DECISION_MEMORY_URL value leaked into {path}"
            )

    # Not an init-time answer: no question, so nothing recorded on update.
    _check_file_contents(
        dst_path / ".copier-answers.agentic.yml",
        unexpect_strs=["decision_memory"],
    )
    _check_file_contents(
        dst_path / "AGENTS.md",
        ["DECISION_MEMORY_URL", "skips recording"],
    )
    _check_file_contents(
        dst_path / "scripts" / "doctor.sh",
        ["DECISION_MEMORY_URL", 'git ls-remote "$DECISION_MEMORY_URL"'],
    )


def test_copier_has_no_decision_memory_question() -> None:
    """The template must never ask for the decision-memory URL at init time."""
    copier_yml = (PROJECT_ROOT / "copier.yml").read_text()
    assert "decision_memory" not in copier_yml


def test_github_forge_ships_lint_workflow_with_prek_job(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub forge + prek: lint workflow with marker check and prek jobs."""
    dst_path = _render(tmp_path, base_answers, "lint-github-prek")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "lint.yml",
        [
            "pull_request",
            "cancel-in-progress: true",
            "git grep -nE",
            "::error::",
            ":!.agents/skills",
            "uvx prek run --all-files --show-diff-on-failure",
            "astral-sh/setup-uv",
            # GitHub expressions must survive Jinja rendering.
            "${{ github.ref }}",
            # commitlint resolves the `extends` preset from the repo
            # directory, so the preset is installed there — a cache-only
            # `npx -p` install dies with MODULE_NOT_FOUND (#137).
            "npm install --no-save --no-audit --no-fund @commitlint/cli@19 @commitlint/config-conventional@19",
            "npx --no-install commitlint --config commitlint.config.mjs",
        ],
    )


def test_lint_workflow_without_prek_keeps_marker_check(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """agentic_precommit=none: marker check stays (guards the updater
    contract), the prek job is omitted."""
    answers = {**base_answers, "agentic_precommit": "none"}
    dst_path = _render(tmp_path, answers, "lint-github-none")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "lint.yml",
        ["git grep -nE", "::error::"],
        unexpect_strs=["prek"],
    )


def test_prek_bootstrap_rendered_and_wired(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Template#53: prek enforcement ships with every prek-enabled render.

    Copier's generation-time `prek install` task does not survive a fresh
    `git clone`, so remote agent sessions need a SessionStart bootstrap.
    """
    dst_path = _render(tmp_path, base_answers, "prek-bootstrap")

    assert (dst_path / "scripts" / "ensure-prek.sh").exists()
    settings = json.loads((dst_path / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for group in settings["hooks"]["SessionStart"]
        for hook in group["hooks"]
    ]
    assert any("ensure-prek.sh" in command for command in commands)
    _check_file_contents(
        dst_path / "AGENTS.md",
        expected_strs=["prek run --all-files", "chore(stub):"],
    )


def test_precommit_none_omits_prek_bootstrap(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    answers = {**base_answers, "agentic_precommit": "none"}
    dst_path = _render(tmp_path, answers, "prek-bootstrap-none")

    assert not (dst_path / "scripts" / "ensure-prek.sh").exists()
    settings = json.loads((dst_path / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for group in settings["hooks"]["SessionStart"]
        for hook in group["hooks"]
    ]
    assert not any("ensure-prek.sh" in command for command in commands)


# ------------------ org plumbing: labels + board auto-add ------------------


def test_github_forge_ships_label_config_and_sync_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Labels are config-as-code so the taxonomy is one file, not seven
    clicked-in sets that drift."""
    dst_path = _render(tmp_path, base_answers, "labels-github")

    _check_file_contents(
        dst_path / ".github" / "labels.toml",
        [
            # Triage taxonomy — the evidence store's `triage` values, so a
            # record and its ticket classify the same way.
            "code-bug",
            "doc-bug",
            "expectation-bug",
            "feature",
            # The quarantine lane's marker (agentic-engineering-template#62).
            "needs-human-review",
            # Kept deliberately: these are what Dependabot applies.
            "dependencies",
            "github_actions",
        ],
    )
    _check_file_contents(
        dst_path / ".github" / "workflows" / "labels.yml",
        [
            # Only fires when the taxonomy itself changes.
            ".github/labels.toml",
            "workflow_dispatch",
            "LABELS_TOKEN",
        ],
    )


def test_label_sync_is_skipped_rather_than_failed_without_its_token(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A repo that never sets the org secret must not show a permanently
    red workflow — but the job has to be visibly SKIPPED, never a silent
    green, or the absence of the sync reads as a successful sync."""
    dst_path = _render(tmp_path, base_answers, "labels-guard")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "labels.yml",
        ["if: ${{ vars.LABELS_SYNC_ENABLED == 'true' }}"],
    )


def test_github_forge_ships_board_auto_add_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub's built-in auto-add is plan-limited to one repository, so
    the board is populated by an action instead."""
    dst_path = _render(tmp_path, base_answers, "board-github")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "add-to-project.yml",
        [
            "actions/add-to-project",
            # The default GITHUB_TOKEN is repo-scoped and cannot write to
            # an ORG-level project.
            "PROJECT_BOARD_TOKEN",
            "vars.PROJECT_BOARD_URL",
            # Fork PRs carry no secrets under `pull_request`; the base-context
            # event is what makes the token reachable. Safe here only because
            # nothing checks out PR code.
            "pull_request_target",
        ],
    )


def test_github_forge_ships_ticket_close_dispatch(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The session-memory store closes a thread when its ticket closes,
    and it cannot hear another repository's events — so every stamped
    repo reports its own closes. The store's location is an org-level
    variable, never baked in at stamping time: the rendered file must
    name no org's store."""
    dst_path = _render(tmp_path, base_answers, "ticket-closed")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "ticket-closed.yml",
        [
            "repos/${STORE}/dispatches",
            "event_type=ticket-closed",
            # The dispatch crosses repositories, which the repo-scoped
            # GITHUB_TOKEN cannot do. The release-bot app signs instead
            # of a PAT: its key already reaches every stamped repo and
            # never expires.
            "actions/create-github-app-token",
            "RELEASE_BOT_PRIVATE_KEY",
            # The minted token reaches the store repo alone.
            "repositories: ${{ steps.store.outputs.name }}",
            # Unset store variable -> visibly SKIPPED, never silent green.
            "if: ${{ vars.SESSION_MEMORY_REPO != '' }}",
        ],
    )
    content = (dst_path / ".github" / "workflows" / "ticket-closed.yml").read_text()
    assert "session-memory" not in content.replace("session-memory store", "")


def test_forgejo_forge_ships_no_github_org_plumbing(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """Both workflows are GitHub-specific: `labels` targets the GitHub
    API and Projects V2 has no Forgejo counterpart."""
    answers = {
        **base_answers,
        "agentic_forge": "forgejo",
        "agentic_forgejo_host": "codeberg.org",
    }
    dst_path = _render(tmp_path, answers, "no-plumbing-forgejo")

    assert not (dst_path / ".github").exists()


def test_consumers_are_told_about_a_release_rather_than_polling_for_it(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A weekly poll means a template release reaches a consumer up to
    seven days later — a timer catching up, not a mechanism. The cron
    stays as the backstop: it is what makes a FAILED dispatch survivable,
    and it covers repos stamped after the fan-out already ran.
    """
    dst_path = _render(tmp_path, base_answers, "dispatch-trigger")

    _check_file_contents(
        dst_path / ".github" / "workflows" / "template-update.yml",
        [
            "repository_dispatch:",
            "types: [template-released]",
            'cron: "17 5 * * 1"',
            "workflow_dispatch:",
        ],
    )


def test_the_weekly_run_fails_loudly_when_the_repo_is_behind(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A green weekly run must mean "on the latest template", not "the
    workflow executed". Being quietly behind is the state the whole
    updater exists to prevent, and it is invisible otherwise.

    Only on `schedule`: the cron is the auditor. A dispatch that fails
    is already visible to whoever triggered it.
    """
    dst_path = _render(tmp_path, base_answers, "update-audit")
    workflow = (dst_path / ".github" / "workflows" / "template-update.yml").read_text()

    assert "github.event_name == 'schedule'" in workflow
    # Both drift causes are named, because the remedy differs: merge the
    # PR, versus go and look at why the fan-out never arrived.
    assert "has not been merged" in workflow
    assert "did not reach this repo" in workflow
    assert "::error::" in workflow


def _detect_forge():
    """The template's own probe, loaded from the extension it ships."""
    return load_module(
        "agentic_ext", PROJECT_ROOT / "extensions" / "agentic.py"
    ).detect_forge()


def _git_repo_with_origin(path: Path, url: str) -> None:
    """A throwaway repo, so `detect_forge()` sees the remote we choose."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=path, check=True)


@pytest.mark.parametrize(
    "origin_url",
    [
        # Detection succeeds — the case that dropped the answer.
        "https://github.com/acme/widget.git",
        # Detection fails — any non-github remote, including the local
        # proxy a sandboxed agent session actually runs behind.
        "http://127.0.0.1:41729/git/acme/widget",
    ],
)
def test_the_forge_answer_is_recorded_however_the_render_environment_looks(
    tmp_path: Path,
    base_answers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    origin_url: str,
) -> None:
    """The answers file is the record of how a repo was stamped, so a
    question whose `when` consults the ENVIRONMENT makes that record
    depend on where the render ran.

    `detect_forge()` shells out to `git remote get-url origin` in the
    process CWD. Gating the question on it meant the answer was written
    when detection failed and dropped when it succeeded — two checkouts
    of one commit producing different answers files. Detection belongs
    in the default, not in the gate.
    """
    cwd = tmp_path / "cwd"
    _git_repo_with_origin(cwd, origin_url)
    monkeypatch.chdir(cwd)

    # Assert the PRECONDITION, not a correlate of it. GitHub reachability
    # is not the question — a git config `insteadOf` rewrite can map every
    # github.com URL to a proxy, which leaves the network fine and the
    # probe permanently blind. A sandboxed agent session is exactly that.
    #
    # Skip rather than pass: a test that cannot reach its own condition
    # must not report the same colour as one that checked it.
    detected = _detect_forge()
    wanted = "github" if "github.com" in origin_url else None
    if detected != wanted:
        pytest.skip(
            f"environment cannot exercise this case: origin {origin_url!r} "
            f"resolves to detect_forge()={detected!r}, wanted {wanted!r}"
        )

    # Deliberately NOT supplied: an explicit value is recorded whatever
    # `when` says, which is exactly what hides the defect. A real update
    # supplies nothing — it reuses the answers file and re-evaluates the
    # gate, so a key recorded last time can silently vanish.
    answers_in = {k: v for k, v in base_answers.items() if k != "agentic_forge"}

    dst_path = _render(tmp_path, answers_in, "forge-recorded")
    answers = (dst_path / ".copier-answers.agentic.yml").read_text()

    assert "agentic_forge: github" in answers


# Globals the Jinja extension injects. Anything here reads the machine
# the render happens on, not the answers.
ENVIRONMENT_PROBES = ("detect_forge", "resolve_repo_owner")


def test_no_question_gate_consults_the_environment() -> None:
    """A `when` clause decides whether an answer is RECORDED, so one that
    probes the machine makes the answers file depend on where the render
    ran — two checkouts of one commit, two different files.

    `agentic_forge` had exactly this: gated on `detect_forge()`, it was
    written when detection failed and dropped when it succeeded. The
    remedy is not specific to that question, so neither is this test.

    Probes belong in `default`, where they seed a first stamp and are
    then superseded by the recorded answer. `agentic_repo_owner` is the
    worked example: same probe, in the default, always recorded.
    """
    questions = yaml.safe_load((PROJECT_ROOT / "copier.yml").read_text())

    offenders = [
        name
        for name, spec in questions.items()
        if isinstance(spec, dict)
        for probe in ENVIRONMENT_PROBES
        if probe in str(spec.get("when", ""))
    ]

    assert offenders == [], (
        f"{offenders}: `when` must depend only on other answers. Move the "
        "probe into `default` so the answer is asked, recorded, and "
        "reproducible."
    )
