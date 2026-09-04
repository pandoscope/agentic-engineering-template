"""The commit-time hooks a generated project ships: the branch-name
gate, the linear-history refusals at all three stages, commitlint, and
the repo-owned hooks the stamped config delegates to.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import copier
import yaml

from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers


def test_branch_name_hook_guards_the_pattern(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A claude/* branch that the gate cannot parse is refused at
    commit time (skills#147): otherwise the ticket gate's
    branch-derived half silently never fires. Other prefixes and a
    repo without the keywords file (Forgejo) pass."""
    dst_path = render_answers(tmp_path, base_answers, "branch-name-hook")

    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        ["id: branch-name", "scripts/check-branch-name.sh"],
    )
    script = dst_path / "scripts" / "check-branch-name.sh"
    assert script.stat().st_mode & 0o111, "hook script must be executable"

    def run_on(branch: str, keywords: bool = True) -> subprocess.CompletedProcess:
        repo = tmp_path / f"repo-{branch.replace('/', '_')}-{keywords}"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        if keywords:
            (repo / ".github").mkdir()
            source = (dst_path / ".github" / "reference-keywords.json").read_text()
            (repo / ".github" / "reference-keywords.json").write_text(source)
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True)
        return subprocess.run([str(script)], cwd=repo, capture_output=True, text=True)

    assert run_on("claude/sk162-session-probe").returncode == 0
    assert run_on("claude/196-precedence").returncode == 0
    assert run_on("claude/7-9-two-tickets").returncode == 0

    bad = run_on("claude/memory-tools-consolidation-h7fnxf")
    assert bad.returncode != 0
    assert "branch_pattern" in bad.stderr

    assert run_on("chore/template-update-v9").returncode == 0
    assert run_on("claude/anything", keywords=False).returncode == 0


def test_linear_history_hooks_refuse_a_merge_into_a_working_branch(  # noqa: PLR0915 — refactor: #243
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A claude/* branch is rebased onto main, never merged into
    (skills#147): the only merge commits are the forge's own. Three
    prek stages share one script — the merge is refused as `git merge`
    would commit it, a conflicted merge finished with `git commit` is
    refused at pre-commit, and the push is the backstop for a merge
    that got past both. A merge commit main already holds passes."""
    dst_path = render_answers(tmp_path, base_answers, "linear-history-hooks")

    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        [
            "default_install_hook_types: [pre-commit, commit-msg, pre-merge-commit, pre-push]",
            "id: linear-history",
            "scripts/check-linear-history.sh",
            "stages: [pre-merge-commit]",
            "stages: [pre-push]",
        ],
    )
    check_file_contents(
        dst_path / ".github" / "workflows" / "lint.yml",
        [
            "linear-history:",
            'name: "linear history (PR commits)"',
            "git rev-list --merges",
        ],
    )
    script = dst_path / "scripts" / "check-linear-history.sh"
    assert script.stat().st_mode & 0o111, "hook script must be executable"

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
    }

    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    def land(repo: Path, branch: str, msg: str) -> None:
        git(repo, "checkout", "-q", branch)
        with (repo / f"{branch.replace('/', '-')}.txt").open("a") as fh:
            fh.write(msg + "\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", msg)
        git(repo, "push", "-q", "origin", branch)

    def diverged(name: str) -> Path:
        """A working branch off main, with main advanced past it on origin."""
        origin = tmp_path / f"{name}.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", origin], check=True
        )
        repo = tmp_path / name
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(repo)],
            check=True,
            capture_output=True,
        )
        git(repo, "checkout", "-q", "-b", "main")
        (repo / "README.md").write_text("seed\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "chore: seed")
        git(repo, "push", "-q", "-u", "origin", "main")
        git(repo, "remote", "set-head", "origin", "main")
        git(repo, "checkout", "-q", "-b", "claude/147-work", "main")
        land(repo, "claude/147-work", "feat: the work")
        land(repo, "main", "feat: a merged PR")
        git(repo, "checkout", "-q", "claude/147-work")
        git(repo, "fetch", "-q", "origin")
        return repo

    def check(repo: Path, mode: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(script), mode],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**env, **extra},
        )

    # --merge: the pre-merge-commit stage, on a working branch.
    repo = diverged("merge")
    refused = check(repo, "--merge")
    assert refused.returncode != 0
    assert "git merge --abort && git rebase origin/main" in refused.stderr
    git(repo, "checkout", "-q", "main")
    assert check(repo, "--merge").returncode == 0, "main is not a working branch"

    # --commit: a conflicted merge being finished by hand.
    repo = diverged("commit")
    assert check(repo, "--commit").returncode == 0, "an ordinary commit passes"
    (repo / ".git" / "MERGE_HEAD").write_text(
        git(repo, "rev-parse", "origin/main") + "\n"
    )
    refused = check(repo, "--commit")
    assert refused.returncode != 0
    assert "git merge --abort && git rebase origin/main" in refused.stderr

    # --push: the backstop, fed the refs prek hands a pre-push hook.
    repo = diverged("push")
    git(repo, "merge", "--no-ff", "origin/main", "-m", "chore: merge main")
    refs = {
        "PRE_COMMIT_LOCAL_BRANCH": "refs/heads/claude/147-work",
        "PRE_COMMIT_TO_REF": git(repo, "rev-parse", "HEAD"),
    }
    refused = check(repo, "--push", **refs)
    assert refused.returncode != 0
    assert "chore: merge main" in refused.stderr
    assert "git rebase origin/main" in refused.stderr
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    git(repo, "rebase", "-q", "origin/main")
    refs["PRE_COMMIT_TO_REF"] = git(repo, "rev-parse", "HEAD")
    assert check(repo, "--push", **refs).returncode == 0, "a linear branch pushes"

    # A forge merge on main is not the branch's: a working branch off
    # a merged main is linear in its own range.
    repo = diverged("forge")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "claude/147-work", "-m", "Merge pull request #1")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "-b", "claude/148-next", "main")
    (repo / "next.txt").write_text("next\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat: next")
    refs = {
        "PRE_COMMIT_LOCAL_BRANCH": "refs/heads/claude/148-next",
        "PRE_COMMIT_TO_REF": git(repo, "rev-parse", "HEAD"),
    }
    assert check(repo, "--push", **refs).returncode == 0


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
    dst_path = render_answers(tmp_path, base_answers, "commitlint-ignores")

    check_file_contents(
        dst_path / "commitlint.config.mjs",
        [
            "defaultIgnores: false",
            "startsWith('Revert \"')",
        ],
        unexpect_strs=['startsWith("Merge'],
    )


def test_repo_owned_hooks_pass_while_the_seeded_config_is_hookless(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """The delegating hook exits 0 on the seeded `repos: []` config (#236).

    prek exits nonzero on a config that defines no hooks, so a freshly
    stamped repo failed `prek run --all-files` out of the box. The entry
    must treat a hookless local config like the no-config case.
    """
    dst_path = render_answers(tmp_path, base_answers, "repo-hooks-empty")

    stamped = yaml.safe_load((dst_path / ".pre-commit-config.yaml").read_text())
    entry = next(
        hook["entry"]
        for repo in stamped["repos"]
        for hook in repo.get("hooks", [])
        if hook.get("id") == "repo-hooks"
    )
    result = subprocess.run(
        [*shlex.split(entry), "README.md"],
        cwd=dst_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_repo_owned_hooks_get_a_seeded_config_of_their_own(
    tmp_path: Path,
    base_answers: dict[str, str],
) -> None:
    """A repo's language hooks live in a file the template seeds once
    and never stamps again (#216).

    The stamped config delegates to it, so the hooks still run on every
    commit, while the vendored-drift check has nothing to judge — the
    repo never has to edit a template-owned file to lint its own code.
    """
    dst_path = render_answers(tmp_path, base_answers, "repo-hooks")

    local = dst_path / ".pre-commit-config.local.yaml"
    check_file_contents(local, ["repos:"])
    stamped = (dst_path / ".pre-commit-config.yaml").read_text()
    assert "repo-hooks" in stamped
    assert ".pre-commit-config.local.yaml" in stamped

    local.write_text("repos:\n  - repo: local\n    hooks: []\n")
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
    assert local.read_text() == "repos:\n  - repo: local\n    hooks: []\n"
