"""The file-length hook every stamped repo runs (#239).

Source files grow without a signal until someone notices. The hook
reads the length of the files a commit touches, fails on an overrun,
and exempts only what the repo's own allowlist names — each entry with
a ticket, so the list shrinks as the tickets close.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import copier
import pytest

from tests.conftest import load_module
from tests.render_support import PROJECT_ROOT, render_answers

SCRIPT = PROJECT_ROOT / "template" / "scripts" / "check_file_length.py"
checker = load_module("check_file_length", SCRIPT)


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


# ------------------------------------------------------- the allowlist


def test_an_entry_carries_a_path_and_the_ticket_that_will_close_it():
    entries, problems = checker.parse_allowlist(
        "# a comment\n\nsrc/big.py  # pandoscope/skills#184\nweb/huge.mjs #12\n"
    )
    assert entries == {"src/big.py": "pandoscope/skills#184", "web/huge.mjs": "#12"}
    assert problems == []


def test_an_entry_without_a_ticket_is_refused():
    """No blanket grandfathering: an exemption nobody is on the hook for
    is a permanent exemption."""
    entries, problems = checker.parse_allowlist("src/big.py\nsrc/ok.py  # later\n")
    assert entries == {}
    assert len(problems) == 2
    assert all("ticket" in problem for problem in problems)
    assert any("src/big.py" in problem for problem in problems)


# ---------------------------------------------------------- the verdict


def test_a_file_over_the_limit_is_named_with_its_length():
    problems = checker.review({"a.py": 801}, 800, {}, set())
    assert len(problems) == 1
    assert "a.py" in problems[0] and "801" in problems[0] and "800" in problems[0]


def test_a_file_at_the_limit_passes():
    assert checker.review({"a.py": 800}, 800, {}, set()) == []


def test_an_allowlisted_overrun_passes():
    assert checker.review({"a.py": 2300}, 800, {"a.py": "#184"}, set()) == []


def test_an_allowlisted_file_back_under_the_limit_must_leave_the_list():
    """The mechanism that makes the list shrink: the commit that brings
    a file under the limit is the one that has to drop its line."""
    problems = checker.review({"a.py": 400}, 800, {"a.py": "#184"}, set())
    assert len(problems) == 1
    assert "a.py" in problems[0] and "remove" in problems[0]


def test_an_allowlist_entry_for_a_file_that_is_gone_must_leave_the_list():
    problems = checker.review({}, 800, {"old.py": "#184"}, {"old.py"})
    assert len(problems) == 1
    assert "old.py" in problems[0] and "remove" in problems[0]


def test_a_file_not_in_this_run_is_not_judged():
    """The hook sees the files a commit touches; an untouched overrun is
    the next commit's problem, not a reason this one cannot land."""
    assert checker.review({}, 800, {}, set()) == []


# ------------------------------------------------------------ end to end


def test_the_hook_fails_on_an_overrun_and_passes_once_it_is_allowlisted(
    tmp_path: Path,
):
    (tmp_path / "big.py").write_text("x = 1\n" * 40)
    red = run("--max", "20", "big.py", cwd=tmp_path)
    assert red.returncode == 1
    assert "big.py" in red.stderr and "40" in red.stderr

    (tmp_path / ".file-length-allowlist").write_text(
        "big.py  # pandoscope/skills#184\n"
    )
    green = run("--max", "20", "big.py", cwd=tmp_path)
    assert green.returncode == 0, green.stderr


def test_a_missing_allowlist_means_no_exemptions_not_an_error(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x = 1\n" * 5)
    assert run("--max", "20", "ok.py", cwd=tmp_path).returncode == 0


def test_a_file_it_cannot_read_as_text_is_skipped(tmp_path: Path):
    (tmp_path / "blob.py").write_bytes(b"\x00\xff" * 4000)
    assert run("--max", "20", "blob.py", cwd=tmp_path).returncode == 0


# --------------------------------------------------------- the stamping


def test_the_stamped_config_runs_the_hook_at_the_answered_limit(
    tmp_path: Path, base_answers: dict[str, str]
):
    dst_path = render_answers(
        tmp_path, {**base_answers, "agentic_max_file_lines": 500}, "limited"
    )
    config = (dst_path / ".pre-commit-config.yaml").read_text()
    assert "scripts/check_file_length.py" in config
    assert "--max" in config and "500" in config
    assert (dst_path / "scripts" / "check_file_length.py").exists()


def test_the_hook_reads_the_languages_the_repo_already_lints(
    tmp_path: Path, base_answers: dict[str, str]
):
    dst_path = render_answers(tmp_path, base_answers, "languages")
    config = (dst_path / ".pre-commit-config.yaml").read_text()
    hook = config[config.index("id: file-length") :]
    files = hook[hook.index("files:") : hook.index("\n", hook.index("files:"))]
    for suffix in ("py", "mjs", "js", "ts", "sh"):
        assert suffix in files


def test_the_allowlist_is_seeded_once_and_never_stamped_again(
    tmp_path: Path, base_answers: dict[str, str]
):
    """The list is the repo's own: what it exempts and which ticket
    closes it cannot survive being overwritten on every update."""
    dst_path = render_answers(tmp_path, base_answers, "seeded-list")
    allowlist = dst_path / ".file-length-allowlist"
    assert allowlist.exists()
    allowlist.write_text("src/legacy.py  # pandoscope/skills#184\n")

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
    assert allowlist.read_text() == "src/legacy.py  # pandoscope/skills#184\n"


@pytest.mark.parametrize("config", [".pre-commit-config.yaml"])
def test_the_template_runs_the_hook_on_itself(config: str):
    """The root config deliberately diverges from the stamped one, so
    the hook is mirrored there by hand or the template never checks its
    own sources."""
    text = (PROJECT_ROOT / config).read_text()
    assert "scripts/check_file_length.py" in text
