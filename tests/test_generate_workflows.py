"""The workflows a generated project ships: template updates, the lint
job and its drift check, and the org plumbing (labels, board, tickets).
"""

from __future__ import annotations

import json
from pathlib import Path


from tests.render_support import check_file_contents, render_answers


def test_github_forge_ships_template_update_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub forge: scheduled copier-update workflow rendered verbatim."""
    dst_path = render_answers(tmp_path, base_answers, "updater-github")

    check_file_contents(
        dst_path / ".github" / "workflows" / "template-update.yml",
        [
            "workflow_dispatch",
            "copier update",
            "--defaults --trust --skip-tasks",
            "copier-template-extensions",
            # --skip-tasks skips the post-stamp glossary prune; the
            # workflow runs the same script explicitly or every update
            # ships orphans (#203).
            "bash scripts/ci/prune_glossary.sh || true",
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
    dst_path = render_answers(tmp_path, answers, "updater-forgejo")

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
    dst_path = render_answers(tmp_path, base_answers, "notify-github")

    check_file_contents(
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
    cleanly there. CLAUDE.md is judged like every other stamped file
    (#213 ruling: fail loudly, never exempt silently); --overwrite only
    gets a terminal-less runner past copier's prompt to that verdict.
    """
    for precommit in ("prek", "none"):
        answers = {**base_answers, "agentic_precommit": precommit}
        dst_path = render_answers(tmp_path, answers, f"lint-drift-{precommit}")

        check_file_contents(
            dst_path / ".github" / "workflows" / "lint.yml",
            [
                "copier recopy",
                '--defaults --trust --skip-tasks --overwrite --vcs-ref "$stamped"',
                "copier-template-extensions",
                # Recopy resurrects the terms the stamp pruned; the drift
                # job prunes again so a converged glossary is not drift
                # (#203).
                "bash scripts/ci/prune_glossary.sh || true",
                "awk '$1 == \"_commit:\" {print $2}'",
                "not a stamped repo",
                "git status --porcelain",
                "template-owned",
            ],
        )


def test_github_forge_ships_lint_workflow_with_prek_job(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub forge + prek: lint workflow with marker check and prek jobs."""
    dst_path = render_answers(tmp_path, base_answers, "lint-github-prek")

    check_file_contents(
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
    dst_path = render_answers(tmp_path, answers, "lint-github-none")

    check_file_contents(
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
    dst_path = render_answers(tmp_path, base_answers, "prek-bootstrap")

    assert (dst_path / "scripts" / "ensure-prek.sh").exists()
    settings = json.loads((dst_path / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for group in settings["hooks"]["SessionStart"]
        for hook in group["hooks"]
    ]
    assert any("ensure-prek.sh" in command for command in commands)
    check_file_contents(
        dst_path / "AGENTS.md",
        expected_strs=["prek run --all-files", "chore(stub):"],
    )


def test_precommit_none_omits_prek_bootstrap(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    answers = {**base_answers, "agentic_precommit": "none"}
    dst_path = render_answers(tmp_path, answers, "prek-bootstrap-none")

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
    dst_path = render_answers(tmp_path, base_answers, "labels-github")

    check_file_contents(
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
    check_file_contents(
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
    dst_path = render_answers(tmp_path, base_answers, "labels-guard")

    check_file_contents(
        dst_path / ".github" / "workflows" / "labels.yml",
        ["if: ${{ vars.LABELS_SYNC_ENABLED == 'true' }}"],
    )


def test_github_forge_ships_board_auto_add_workflow(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """GitHub's built-in auto-add is plan-limited to one repository, so
    the board is populated by an action instead."""
    dst_path = render_answers(tmp_path, base_answers, "board-github")

    check_file_contents(
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
    dst_path = render_answers(tmp_path, base_answers, "ticket-closed")

    check_file_contents(
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
    dst_path = render_answers(tmp_path, answers, "no-plumbing-forgejo")

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
    dst_path = render_answers(tmp_path, base_answers, "dispatch-trigger")

    check_file_contents(
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
    dst_path = render_answers(tmp_path, base_answers, "update-audit")
    workflow = (dst_path / ".github" / "workflows" / "template-update.yml").read_text()

    assert "github.event_name == 'schedule'" in workflow
    # Both drift causes are named, because the remedy differs: merge the
    # PR, versus go and look at why the fan-out never arrived.
    assert "has not been merged" in workflow
    assert "did not reach this repo" in workflow
    assert "::error::" in workflow
