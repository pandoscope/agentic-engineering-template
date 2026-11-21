"""The identity hook (#189, #313): no PUSH_BLOCKLIST value in what a
commit or a push publishes, and no silent pass when the list is absent.

The fixtures carry made-up values only, and every assertion on the
hook's output checks that the value is ABSENT — the hook names the
surface and the entry's placeholder, never the match.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.render_support import PROJECT_ROOT, check_file_contents, render_answers

SCRIPT = PROJECT_ROOT / "template" / "scripts" / "check-push-blocklist.sh"

LOGIN = "zq-fakelogin"
NAME = "Zq Fakename"
MAIL = "zq@fake.example"
LIST = f"{LOGIN}=pb:old-account|{NAME}|{MAIL}=pb:mail"


def run(
    *args: str, cwd: Path | None = None, **env: str | None
) -> subprocess.CompletedProcess:
    """The hook with ONLY the given variables; a None value leaves one unset."""
    full = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    full.update({k: v for k, v in env.items() if v is not None})
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=full,
        check=False,
    )


def value_silent(proc: subprocess.CompletedProcess) -> None:
    out = (proc.stdout + proc.stderr).lower()
    for value in (LOGIN, NAME, MAIL):
        assert value.lower() not in out, "the hook printed a blocklist value"


def test_unset_fails_and_says_how_to_load(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello\n")
    proc = run("--files", str(f), PUSH_BLOCKLIST=None)
    assert proc.returncode == 1
    assert "PUSH_BLOCKLIST is unset or empty" in proc.stderr
    assert "source" in proc.stderr and "PUSH_BLOCKLIST_OFF=1" in proc.stderr


def test_empty_and_whitespace_or_pipes_only_fail_alike(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello\n")
    for value in ("", " | |  ", "||\n", "\t"):
        proc = run("--files", str(f), PUSH_BLOCKLIST=value)
        assert proc.returncode == 1, repr(value)
        assert "unset or empty" in proc.stderr, repr(value)
    proc = run("--message", str(f), PUSH_BLOCKLIST="|")
    assert proc.returncode == 1
    assert "unset or empty" in proc.stderr


def test_opt_out_is_explicit_and_announced(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text(f"{LOGIN}\n")
    proc = run("--files", str(f), PUSH_BLOCKLIST=None, PUSH_BLOCKLIST_OFF="1")
    assert proc.returncode == 0
    assert "PUSH_BLOCKLIST_OFF=1" in proc.stdout
    value_silent(proc)
    # Any other value is not the opt-out.
    proc = run("--files", str(f), PUSH_BLOCKLIST=None, PUSH_BLOCKLIST_OFF="yes")
    assert proc.returncode == 1


def test_a_hit_in_a_file_names_file_and_placeholder_only(tmp_path: Path) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see\n")
    dirty = tmp_path / "dirty.txt"
    dirty.write_text(f"by {LOGIN.upper()} today\n")
    proc = run("--files", str(clean), str(dirty), PUSH_BLOCKLIST=LIST)
    assert proc.returncode == 1
    assert f"{dirty} carries PUSH_BLOCKLIST pb:old-account" in proc.stderr
    assert "clean.txt" not in proc.stderr
    value_silent(proc)


def test_a_hit_in_the_message_names_surface_and_placeholder_only(
    tmp_path: Path,
) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(f"feat: thanks to {NAME}\n\nmail {MAIL}\n")
    proc = run("--message", str(msg), PUSH_BLOCKLIST=LIST)
    assert proc.returncode == 1
    assert "the commit message carries PUSH_BLOCKLIST entry 2" in proc.stderr
    assert "the commit message carries PUSH_BLOCKLIST pb:mail" in proc.stderr
    value_silent(proc)


def test_an_early_hit_in_a_large_text_is_not_lost(tmp_path: Path) -> None:
    """grep -q leaves at its first match; a pipe writer still feeding a
    large text then dies of SIGPIPE, which pipefail turned into "no
    match". The hit at the top of a multi-megabyte text must stand."""
    msg = tmp_path / "big.txt"
    msg.write_text(f"{LOGIN} at the top\n" + ("a" * 99 + "\n") * 30000)
    for _ in range(3):
        proc = run("--message", str(msg), PUSH_BLOCKLIST=LIST)
        assert proc.returncode == 1
        assert "pb:old-account" in proc.stderr


def test_a_file_name_is_a_surface_and_is_never_printed(tmp_path: Path) -> None:
    named = tmp_path / f"notes-{LOGIN}.txt"
    named.write_text(f"mail {MAIL}\n")
    proc = run("--files", str(named), PUSH_BLOCKLIST=LIST)
    assert proc.returncode == 1
    assert (
        "the path of staged file 1 carries PUSH_BLOCKLIST pb:old-account" in proc.stderr
    )
    assert "staged file 1 carries PUSH_BLOCKLIST pb:mail" in proc.stderr
    value_silent(proc)


def test_non_ascii_values_match_in_any_casing_under_a_c_locale(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("von JÜRGEN geschrieben\n", encoding="utf-8")
    proc = run("--files", str(f), PUSH_BLOCKLIST="jürgen=pb:name", LC_ALL="C")
    assert proc.returncode == 1
    assert "pb:name" in proc.stderr


def test_opt_out_is_visible_under_prek(
    tmp_path: Path, base_answers: dict[str, str]
) -> None:
    """prek hides a passing hook's output unless the hook is verbose,
    and the announcement IS that output."""
    dst = render_answers(tmp_path, base_answers, "blocklist-verbose")
    config = (dst / ".pre-commit-config.yaml").read_text()
    for hook in ("push-blocklist\n", "push-blocklist-msg\n", "push-blocklist-push\n"):
        _, tail = config.split(f"id: {hook}", 1)
        assert "verbose: true" in tail.split("- id:")[0], hook


def test_no_hit_passes_quietly(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello\n")
    proc = run("--files", str(f), PUSH_BLOCKLIST=LIST)
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""


def test_the_label_is_metadata_never_a_pattern(tmp_path: Path) -> None:
    """`value=pb:name` entries: a file merely MENTIONING the placeholder
    passes, and the value still fails."""
    f = tmp_path / "a.txt"
    f.write_text("scrubbed to pb:old-account\n")
    assert run("--files", str(f), PUSH_BLOCKLIST=LIST).returncode == 0
    f.write_text(f"{LOGIN} was here\n")
    assert run("--files", str(f), PUSH_BLOCKLIST=LIST).returncode == 1


def test_editor_added_newlines_and_stray_pipes_are_tolerated(tmp_path: Path) -> None:
    """The list comes from an editor-saved file, and editors add a final
    newline for good reason: a raw newline or a dangling pipe is no
    entry, never an empty one that matches everything."""
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see\n")
    dirty = tmp_path / "dirty.txt"
    dirty.write_text(f"contact {LOGIN} today\n")
    for messy in (
        f"{LOGIN}|bob\n",
        f"{LOGIN}||bob",
        f"|{LOGIN}|bob|",
        f"{LOGIN}|bob\r\n",
    ):
        assert run("--files", str(clean), PUSH_BLOCKLIST=messy).returncode == 0, repr(
            messy
        )
        assert run("--files", str(dirty), PUSH_BLOCKLIST=messy).returncode == 1, repr(
            messy
        )


def _git(repo: Path, *args: str, **env: str) -> str:
    base = {
        "PATH": os.environ["PATH"],
        "HOME": str(repo.parent),
        "GIT_AUTHOR_NAME": "dev",
        "GIT_AUTHOR_EMAIL": "dev@example.org",
        "GIT_COMMITTER_NAME": "dev",
        "GIT_COMMITTER_EMAIL": "dev@example.org",
    }
    base.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=base,
    ).stdout.strip()


def pushed_repo(tmp_path: Path) -> Path:
    """A clone with main pushed and three local commits beyond it: one
    carrying a value in its message, one in its author name, one in the
    added side of its diff."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
    repo = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True
    )
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "a.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: seed")
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "checkout", "-q", "-b", "claude/313-work")
    (repo / "a.txt").write_text("seed\nmore\n")
    _git(repo, "commit", "-q", "-am", f"feat: credit {LOGIN}")
    (repo / "a.txt").write_text("seed\nmore\nstill fine\n")
    _git(repo, "commit", "-q", "-am", "feat: by someone", GIT_AUTHOR_NAME=NAME)
    (repo / "b.txt").write_text(f"contact {MAIL}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: contact")
    return repo


def test_pre_push_judges_message_identity_and_added_diff(tmp_path: Path) -> None:
    repo = pushed_repo(tmp_path)
    shas = _git(repo, "rev-list", "--reverse", "origin/main..HEAD").split()
    short = [_git(repo, "rev-parse", "--short", s) for s in shas]
    proc = run(
        "--push",
        cwd=repo,
        PUSH_BLOCKLIST=LIST,
        PRE_COMMIT_FROM_REF=_git(repo, "rev-parse", "origin/main"),
        PRE_COMMIT_TO_REF="HEAD",
        PRE_COMMIT_REMOTE_NAME="origin",
    )
    assert proc.returncode == 1
    assert (
        f"commit {short[0]} message carries PUSH_BLOCKLIST pb:old-account"
        in proc.stderr
    )
    assert (
        f"commit {short[1]} author/committer identity carries PUSH_BLOCKLIST entry 2"
        in proc.stderr
    )
    assert f"commit {short[2]} added diff carries PUSH_BLOCKLIST pb:mail" in proc.stderr
    assert "3 hit(s)" in proc.stderr
    value_silent(proc)


def test_pre_push_of_a_new_branch_falls_back_to_the_unpushed_range(
    tmp_path: Path,
) -> None:
    """prek hands a new branch the empty tree as FROM_REF (no remote
    tip); the hook then judges what the remote does not hold."""
    repo = pushed_repo(tmp_path)
    empty_tree = _git(repo, "hash-object", "-t", "tree", "/dev/null")
    proc = run(
        "--push",
        cwd=repo,
        PUSH_BLOCKLIST=LIST,
        PRE_COMMIT_FROM_REF=empty_tree,
        PRE_COMMIT_TO_REF="HEAD",
    )
    assert proc.returncode == 1
    assert "3 hit(s)" in proc.stderr
    # The pushed seed commit (clean) and nothing else on origin is judged.
    proc = run("--push", cwd=repo, PUSH_BLOCKLIST="nothing-matches-here")
    assert proc.returncode == 0


def test_pre_push_fails_closed_when_git_cannot_read_the_range(tmp_path: Path) -> None:
    """A git call that fails must refuse the push, never judge zero
    commits and pass (the same class of bug as a missing list)."""
    repo = pushed_repo(tmp_path)
    proc = run("--push", cwd=repo, PUSH_BLOCKLIST=LIST, PRE_COMMIT_TO_REF="no-such-ref")
    assert proc.returncode != 0
    assert "refusing to judge the push" in proc.stderr
    value_silent(proc)


def test_pre_push_judges_an_annotated_tag_and_the_ref_name(tmp_path: Path) -> None:
    repo = pushed_repo(tmp_path)
    _git(repo, "tag", "-a", "v9", "-m", f"release by {LOGIN}", GIT_COMMITTER_NAME=NAME)
    proc = run(
        "--push",
        cwd=repo,
        PUSH_BLOCKLIST=LIST,
        PRE_COMMIT_LOCAL_BRANCH="refs/tags/v9",
        PRE_COMMIT_TO_REF=_git(repo, "rev-parse", "v9"),
    )
    assert proc.returncode == 1
    assert (
        "tag v9 tagger and message carries PUSH_BLOCKLIST pb:old-account" in proc.stderr
    )
    assert "tag v9 tagger and message carries PUSH_BLOCKLIST entry 2" in proc.stderr
    # The tag's commits are judged too, through the peeled commit.
    assert "added diff carries PUSH_BLOCKLIST pb:mail" in proc.stderr
    value_silent(proc)
    proc = run(
        "--push",
        cwd=repo,
        PUSH_BLOCKLIST=LIST,
        PRE_COMMIT_LOCAL_BRANCH=f"refs/heads/claude/1-{LOGIN}",
        PRE_COMMIT_FROM_REF=_git(repo, "rev-parse", "HEAD"),
        PRE_COMMIT_TO_REF="HEAD",
    )
    assert proc.returncode == 1
    assert "the pushed ref name carries PUSH_BLOCKLIST pb:old-account" in proc.stderr
    value_silent(proc)


def test_binary_files_are_judged_too(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01PNG" + LOGIN.encode() + b"\x00\xff")
    proc = run("--files", str(f), PUSH_BLOCKLIST=LIST)
    assert proc.returncode == 1
    assert "pb:old-account" in proc.stderr
    value_silent(proc)


def test_pre_push_judges_changed_paths_and_passes_a_deleted_ref(tmp_path: Path) -> None:
    repo = pushed_repo(tmp_path)
    (repo / f"{LOGIN}.md").write_text("fine\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs: fine")
    proc = run(
        "--push",
        cwd=repo,
        PUSH_BLOCKLIST=f"{LOGIN}=pb:old-account",
        PRE_COMMIT_FROM_REF=_git(repo, "rev-parse", "HEAD~1"),
        PRE_COMMIT_TO_REF="HEAD",
    )
    assert proc.returncode == 1
    assert "changed paths carries PUSH_BLOCKLIST pb:old-account" in proc.stderr
    value_silent(proc)
    proc = run(
        "--push", cwd=repo, PUSH_BLOCKLIST="nothing-here", PRE_COMMIT_TO_REF="0" * 40
    )
    assert proc.returncode == 0


def test_pre_push_without_the_list_fails_too(tmp_path: Path) -> None:
    repo = pushed_repo(tmp_path)
    proc = run("--push", cwd=repo, PUSH_BLOCKLIST=None)
    assert proc.returncode == 1
    assert "what this push publishes" in proc.stderr


def test_rendered_project_wires_all_three_stages(
    tmp_path: Path, base_answers: dict[str, str]
) -> None:
    dst = render_answers(tmp_path, base_answers, "blocklist-hook")
    check_file_contents(
        dst / ".pre-commit-config.yaml",
        [
            "id: push-blocklist\n",
            "scripts/check-push-blocklist.sh --files",
            "id: push-blocklist-msg",
            "scripts/check-push-blocklist.sh --message",
            "id: push-blocklist-push",
            "scripts/check-push-blocklist.sh --push",
            "stages: [pre-push]",
        ],
        ["PUSH_BLOCKLIST:-"],
    )
    script = dst / "scripts" / "check-push-blocklist.sh"
    assert script.stat().st_mode & 0o111, "hook script must be executable"
    check_file_contents(
        dst / "AGENTS.md", ["### Leak Prevention", "PUSH_BLOCKLIST_OFF=1"]
    )
    check_file_contents(
        dst / ".github" / "workflows" / "lint.yml", ['PUSH_BLOCKLIST_OFF: "1"']
    )
    check_file_contents(dst / "scripts" / "doctor.sh", ["PUSH_BLOCKLIST"])
