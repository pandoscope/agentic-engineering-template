"""The file-cap hook every stamped repo runs (#239, #242).

The cap is what an agent pays to read the file: an estimated token
count (bytes / 4), so comments and blank lines count and the language
does not matter. Complexity is measured independently — ruff carries it
for Python, and for bash (which ruff cannot read) the same hook counts
code lines, because long bash is a rewrite-in-Python signal and a
total-line measure would punish the comments that make bash safer.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
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


# --------------------------------------------------------- the measure


def test_tokens_are_estimated_from_bytes(tmp_path: Path):
    """bytes / 4, rounded up: dependency-free, monotone, and measuring
    the whole file — comments cost a reader tokens too."""
    path = tmp_path / "a.md"
    path.write_bytes(b"x" * 41)
    tokens, code, kind = checker.measure(str(path))
    assert tokens == 11
    assert code is None and kind is None


def test_bash_files_also_get_a_code_line_count(tmp_path: Path):
    """Blank lines and comments stay free for bash: they are the
    mitigation the research recommends, not the complexity."""
    path = tmp_path / "a.sh"
    path.write_text(
        "#!/bin/sh\n"
        "# quoting intentional: paths may contain spaces\n"
        "\n"
        'echo "one"\n'
        "  \n"
        'echo "two"  # trailing comments do not make a line free\n'
    )
    tokens, code, kind = checker.measure(str(path))
    assert code == 2 and kind == "sh"
    assert tokens > 0


def test_python_and_js_files_get_code_line_counts_too(tmp_path: Path):
    """Code lines are the split signal for every code language; the
    comment heuristic is line-based, which is all a hard limit needs."""
    py = tmp_path / "a.py"
    py.write_text("# comment\n\nx = 1\ny = 2\n")
    assert checker.measure(str(py))[1:] == (2, "code")
    mjs = tmp_path / "a.mjs"
    mjs.write_text("// c\n/* block\n * c\n */\nlet x = 1\n\nlet y = 2\n")
    assert checker.measure(str(mjs))[1:] == (2, "code")
    jinja = tmp_path / "b.py.jinja"
    jinja.write_text("x = 1\n")
    assert checker.measure(str(jinja))[2] == "code"


def test_a_file_it_cannot_read_as_text_is_not_measured(tmp_path: Path):
    path = tmp_path / "blob.py"
    path.write_bytes(b"\x00\xff" * 4000)
    assert checker.measure(str(path)) is None


# ---------------------------------------------------------- the verdict


def test_a_file_over_the_token_cap_is_named_with_its_estimate():
    problems = checker.review({"a.md": (10001, None, None)}, 10000, 150, 500, {}, set())
    assert len(problems) == 1
    assert "a.md" in problems[0] and "10001" in problems[0]
    assert "token" in problems[0]


def test_a_file_at_the_token_cap_passes():
    assert (
        checker.review({"a.md": (10000, None, None)}, 10000, 150, 500, {}, set()) == []
    )


def test_a_bash_file_over_the_code_line_limit_points_at_python():
    """Within the token cap but over the code-line limit: the fix for
    long bash is not splitting it."""
    problems = checker.review({"x.sh": (500, 151, "sh")}, 10000, 150, 500, {}, set())
    assert len(problems) == 1
    assert "x.sh" in problems[0] and "151" in problems[0]
    assert "Python" in problems[0]


def test_a_bash_file_at_the_code_line_limit_passes():
    assert checker.review({"x.sh": (500, 150, "sh")}, 10000, 150, 500, {}, set()) == []


def test_an_allowlisted_overrun_passes():
    measures = {"a.py": (12000, 300, "code"), "x.sh": (500, 200, "sh")}
    allowlist = {"a.py": "#184", "x.sh": "#185"}
    assert checker.review(measures, 10000, 150, 500, allowlist, set()) == []


def test_an_allowlisted_file_back_under_every_limit_must_leave_the_list():
    """The mechanism that makes the list shrink: the commit that brings
    a file under the caps is the one that has to drop its line."""
    problems = checker.review(
        {"a.py": (400, 90, "code")}, 10000, 150, 500, {"a.py": "#184"}, set()
    )
    assert len(problems) == 1
    assert "a.py" in problems[0] and "remove" in problems[0]


def test_an_allowlisted_bash_file_still_over_one_limit_keeps_its_line():
    measures = {"x.sh": (500, 200, "sh")}
    assert checker.review(measures, 10000, 150, 500, {"x.sh": "#185"}, set()) == []


def test_an_allowlist_entry_for_a_file_that_is_gone_must_leave_the_list():
    problems = checker.review({}, 10000, 150, 500, {"old.py": "#184"}, {"old.py"})
    assert len(problems) == 1
    assert "old.py" in problems[0] and "remove" in problems[0]


def test_a_file_not_in_this_run_is_not_judged():
    """The hook sees the files a commit touches; an untouched overrun is
    the next commit's problem, not a reason this one cannot land."""
    assert checker.review({}, 10000, 150, 500, {}, set()) == []


def test_a_code_file_over_the_code_line_limit_is_told_to_split():
    problems = checker.review({"a.py": (500, 501, "code")}, 10000, 150, 500, {}, set())
    assert len(problems) == 1
    assert "a.py" in problems[0] and "501" in problems[0] and "split" in problems[0]


def test_a_code_file_at_the_code_line_limit_passes():
    assert (
        checker.review({"a.py": (500, 500, "code")}, 10000, 150, 500, {}, set()) == []
    )


# ------------------------------------------------------------ end to end


def test_the_hook_fails_on_an_overrun_and_passes_once_it_is_allowlisted(
    tmp_path: Path,
):
    (tmp_path / "big.py").write_text("x = 1\n" * 40)  # 240 bytes, ~60 tokens
    red = run("--max-tokens", "20", "big.py", cwd=tmp_path)
    assert red.returncode == 1
    assert "big.py" in red.stderr and "60" in red.stderr

    (tmp_path / ".file-length-allowlist").write_text(
        "big.py  # pandoscope/skills#184\n"
    )
    green = run("--max-tokens", "20", "big.py", cwd=tmp_path)
    assert green.returncode == 0, green.stderr


def test_the_bash_limit_counts_code_not_comments(tmp_path: Path):
    (tmp_path / "x.sh").write_text(
        "#!/bin/sh\n" + "# a comment\n" * 40 + 'echo "a"\necho "b"\necho "c"\n'
    )
    red = run("--max-sh-code-lines", "2", "x.sh", cwd=tmp_path)
    assert red.returncode == 1 and "x.sh" in red.stderr
    green = run("--max-sh-code-lines", "3", "x.sh", cwd=tmp_path)
    assert green.returncode == 0, green.stderr


def test_the_code_line_limit_counts_code_not_comments(tmp_path: Path):
    (tmp_path / "a.py").write_text("# c\n" * 40 + "x = 1\ny = 2\nz = 3\n")
    red = run("--max-code-lines", "2", "a.py", cwd=tmp_path)
    assert red.returncode == 1 and "a.py" in red.stderr
    green = run("--max-code-lines", "3", "a.py", cwd=tmp_path)
    assert green.returncode == 0, green.stderr


def test_a_missing_allowlist_means_no_exemptions_not_an_error(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x = 1\n" * 5)
    assert run("--max-tokens", "20", "ok.py", cwd=tmp_path).returncode == 0


def test_a_file_it_cannot_read_as_text_is_skipped(tmp_path: Path):
    (tmp_path / "blob.py").write_bytes(b"\x00\xff" * 4000)
    assert run("--max-tokens", "20", "blob.py", cwd=tmp_path).returncode == 0


# --------------------------------------------------------- the stamping


def test_the_stamped_config_runs_the_hook_at_the_answered_cap(
    tmp_path: Path, base_answers: dict[str, str]
):
    dst_path = render_answers(
        tmp_path, {**base_answers, "agentic_max_file_tokens": 5000}, "capped"
    )
    config = (dst_path / ".pre-commit-config.yaml").read_text()
    assert "scripts/check_file_length.py" in config
    assert "--max-tokens" in config and "5000" in config
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


def test_the_hook_reads_everything_else_a_model_reads(
    tmp_path: Path, base_answers: dict[str, str]
):
    """The token cap is about read cost, which Markdown, YAML and JSON
    incur like any source file; generated lockfiles and minified assets
    are excluded — nobody reads them whole."""
    dst_path = render_answers(tmp_path, base_answers, "read-cost")
    config = (dst_path / ".pre-commit-config.yaml").read_text()
    hook = config[config.index("id: file-length") :]
    block = hook[: hook.index("- id:", 5)]
    for suffix in ("md", "yml", "yaml", "toml", "json", "html", "css"):
        assert suffix in block
    assert "exclude:" in block
    assert "lock" in block and "min" in block


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
    assert "--max-tokens" in text


# ------------------------------------------------- complexity, independently


def test_the_template_repo_measures_python_complexity_with_ruff():
    """The capacity cap deliberately ignores complexity — ruff carries
    that side for Python, at the thresholds the research located the
    correctness cliff (#242)."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    lint = pyproject["tool"]["ruff"]["lint"]
    assert "C901" in lint["extend-select"]
    assert "PLR0915" in lint["extend-select"]
    assert lint["mccabe"]["max-complexity"] == 20
