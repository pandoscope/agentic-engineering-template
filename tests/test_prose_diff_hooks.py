"""The diff-scoped prose hooks a generated project ships (#275): the
reflow guard at commit-msg and in the PR range, and the guillemet
hook at pre-commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.render_support import check_file_contents, render_answers

PARAGRAPH = "The runner starts every step in order.\nIt logs each exit code.\n"
REFLOWED = "The runner starts every step\nin order. It logs each exit code.\n"
REWORDED = "The runner starts every step\nin order. It logs every exit code.\n"
COMMENT = "# The runner starts every step in order.\n# It logs each exit code.\nx = 1\n"
COMMENT_REFLOWED = (
    "# The runner starts every step\n# in order. It logs each exit code.\nx = 1\n"
)


@pytest.fixture(scope="module")
def script(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # One render for the module: every test drives the same script.
    answers = {
        "agentic_project_name": "Snake Farm",
        "agentic_project_description": "A sample Snake farming project.",
        "agentic_project_slug": "snake-farm",
        "agentic_precommit": "prek",
        "agentic_forge": "github",
        "agentic_repo_owner": "actions-user",
        "agentic_merge_approvers": "actions-user",
    }
    tmp_path = tmp_path_factory.mktemp("prose-diff")
    dst_path = render_answers(tmp_path, answers, "prose-diff-hooks")
    check_file_contents(
        dst_path / ".pre-commit-config.yaml",
        ["id: reflow-guard", "id: guillemets-in-code", "scripts/check_prose_diff.py"],
    )
    check_file_contents(
        dst_path / ".github" / "workflows" / "lint.yml",
        ["scripts/check_prose_diff.py --range"],
    )
    return dst_path / "scripts" / "check_prose_diff.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def repo_with(tmp_path: Path, name: str, content: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "docs: seed")
    return repo


def stage(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    git(repo, "add", name)


def reflow_at_commit(
    script: Path, repo: Path, subject: str
) -> subprocess.CompletedProcess:
    msg = repo / ".git" / "COMMIT_EDITMSG"
    msg.write_text(subject + "\n")
    return subprocess.run(
        ["python3", str(script), "reflow", "--msg", str(msg)],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_pure_rewrap_fails_outside_style(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "README.md", PARAGRAPH)
    stage(repo, "README.md", REFLOWED)
    result = reflow_at_commit(script, repo, "docs: explain the runner")
    assert result.returncode != 0
    assert "README.md" in result.stderr
    assert "reflow" in result.stderr


def test_pure_rewrap_passes_in_style_commit(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "README.md", PARAGRAPH)
    stage(repo, "README.md", REFLOWED)
    assert (
        reflow_at_commit(script, repo, "style(docs): semantic line breaks").returncode
        == 0
    )


def test_one_changed_word_is_an_edit(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "README.md", PARAGRAPH)
    stage(repo, "README.md", REWORDED)
    assert reflow_at_commit(script, repo, "docs: explain the runner").returncode == 0


def test_rewrapped_code_comment_is_a_reflow(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "run.py", COMMENT)
    stage(repo, "run.py", COMMENT_REFLOWED)
    assert reflow_at_commit(script, repo, "docs: explain the runner").returncode != 0


def test_template_update_carries_upstream_reflows(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "README.md", PARAGRAPH)
    (repo / ".copier-answers.agentic.yml").write_text("_commit: v2\n")
    git(repo, "add", ".copier-answers.agentic.yml")
    stage(repo, "README.md", REFLOWED)
    assert (
        reflow_at_commit(script, repo, "chore(template): update to v2").returncode == 0
    )


def test_range_checks_each_commit_by_its_own_subject(
    script: Path, tmp_path: Path
) -> None:
    repo = repo_with(tmp_path, "README.md", PARAGRAPH)
    base = git(repo, "rev-parse", "HEAD").strip()
    stage(repo, "README.md", REFLOWED)
    git(repo, "commit", "-q", "--no-verify", "-m", "style: rewrap")
    ok = subprocess.run(
        ["python3", str(script), "--range", f"{base}..HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    stage(repo, "README.md", PARAGRAPH)
    git(repo, "commit", "-q", "--no-verify", "-m", "docs: rewrap back")
    bad = subprocess.run(
        ["python3", str(script), "--range", f"{base}..HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0
    assert "docs: rewrap back" in bad.stderr


@pytest.mark.parametrize("name", ["tool.py", "tool.mjs", "tool.sh"])
def test_guillemet_in_code_fails(script: Path, tmp_path: Path, name: str) -> None:
    repo = repo_with(tmp_path, name, "x = 1\n")
    stage(repo, name, "x = 1\n# pass «name» here\n")
    result = subprocess.run(
        ["python3", str(script), "guillemets", name],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert name in result.stderr


def test_guillemet_in_markdown_passes(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "notes.md", "text\n")
    stage(repo, "notes.md", "text\nWrite `[«short-sha»](«url»)`.\n")
    result = subprocess.run(
        ["python3", str(script), "guillemets", "notes.md"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_guillemet_already_on_main_passes(script: Path, tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "tool.py", "# old «name»\n")
    stage(repo, "tool.py", "# old «name»\nx = 1\n")
    result = subprocess.run(
        ["python3", str(script), "guillemets", "tool.py"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
