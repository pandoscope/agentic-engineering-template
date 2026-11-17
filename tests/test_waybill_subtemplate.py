"""Render and contract tests for the waybill subtemplate.

Selected by `agentic_subtemplate=waybill`, it stamps an org's ORDER repo:
the orders whose pull requests fire review sessions,
the dispositions that answer their findings,
and the check that validates every order before a session reads it.
"""

from __future__ import annotations

import re
from pathlib import Path

import copier
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
SUBTEMPLATE = PROJECT_ROOT / "waybill"

WAYBILL_FILES = frozenset(
    {
        ".copier-answers.agentic.yml",
        ".github/workflows/order-check.yml",
        ".github/workflows/template-update.yml",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "dispositions/README.md",
        "scripts/ci/prune_glossary.sh",
        "scripts/ci/template-update-body.sh",
        "shortcodes.json",
    }
)

# Pandoscope is named on purpose (the one-way rule, the order check's tool),
# and `my-org` is the README's invented example; any other owner is a leak.
ALLOWED_OWNERS = frozenset({"pandoscope", "my-org", "org"})


def _render_waybill(tmp_path: Path) -> Path:
    dst_path = tmp_path / "waybill"
    copier.run_copy(
        src_path=str(PROJECT_ROOT),
        dst_path=dst_path,
        data={"agentic_subtemplate": "waybill"},
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        # Pin HEAD: with release tags present locally,
        # copier would render the latest RELEASE instead of this branch.
        vcs_ref="HEAD",
    )
    return dst_path


def _recopy(dst_path: Path) -> None:
    copier.run_recopy(
        dst_path=dst_path,
        answers_file=".copier-answers.agentic.yml",
        defaults=True,
        unsafe=True,
        skip_tasks=True,
        overwrite=True,
        vcs_ref="HEAD",
    )


def test_waybill_render_produces_exactly_the_waybill_files(tmp_path: Path) -> None:
    dst_path = _render_waybill(tmp_path)
    rendered = {
        path.relative_to(dst_path).as_posix()
        for path in dst_path.rglob("*")
        if path.is_file()
    }
    assert rendered == WAYBILL_FILES


def test_the_answers_file_records_nothing_about_the_org(tmp_path: Path) -> None:
    """The waybill needs nothing from the org to work.
    Its texts speak of "this org", so no org name is stamped in and none can go stale."""
    dst_path = _render_waybill(tmp_path)
    answers = yaml.safe_load((dst_path / ".copier-answers.agentic.yml").read_text())

    assert answers["agentic_subtemplate"] == "waybill"
    assert set(answers) <= {
        "_commit",
        "_src_path",
        "agentic_subtemplate",
        "agentic_actions_runner",
    }


def test_the_waybill_names_no_organisation(tmp_path: Path) -> None:
    """Every repository a stamped file names belongs to Pandoscope or to the invented example.
    A literal org would be wrong for every other org that stamps a waybill."""
    dst_path = _render_waybill(tmp_path)
    owner = r"([A-Za-z0-9][A-Za-z0-9-]*)"
    references = [
        re.compile(
            rf"\b{owner}/[A-Za-z0-9._-]+(?:#\d+|:)"
        ),  # org/repo#4, org/repo: ref
        re.compile(rf"github\.com/{owner}"),  # an address on GitHub
        re.compile(rf"`{owner}/[A-Za-z0-9._-]+`"),  # `org/repo` in a code span
    ]
    paths = {part for relpath in WAYBILL_FILES for part in Path(relpath).parts}
    for relpath in sorted(WAYBILL_FILES - {".copier-answers.agentic.yml"}):
        text = (dst_path / relpath).read_text()
        owners = {m.group(1) for ref in references for m in ref.finditer(text)} - paths
        assert owners <= ALLOWED_OWNERS, f"{relpath} names {owners - ALLOWED_OWNERS}"


def test_the_readme_states_the_order_contract(tmp_path: Path) -> None:
    """An order is a branch, a file and a pull request, and its name decides which Routine fires.
    The name ends in the repo's shortcode and the pull request's number,
    because every repo numbers its pull requests from 1."""
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "order/<name>" in readme
    assert "orders/<name>.yml" in readme
    assert "review-<pass>-<model_tier>-<code><n>" in readme
    assert "shortcodes.json" in readme
    assert "dispositions/<name>.yml" in readme
    assert "## One way only" in readme


def test_the_dispositions_readme_states_the_format(tmp_path: Path) -> None:
    """Findings are answered here, not in forge comments, one file per order."""
    text = (_render_waybill(tmp_path) / "dispositions" / "README.md").read_text()

    assert "reviewed_head:" in text
    assert (
        'status: open           # or "fixed <org/repo@sha>", or "no-commit: <why>"'
        in text
    )


def test_the_readme_describes_every_field_of_the_order_schema(tmp_path: Path) -> None:
    """A reader fills an order in from the README alone:
    the name's pattern stands on its own line, and every field the schema knows has an entry."""
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "\n`review-<pass>-<model_tier>-<code><n>`\n" in readme
    for field in (
        "id",
        "role",
        "pass",
        "model_tier",
        "pull_request",
        "base",
        "checkouts",
        "tickets",
        "result",
    ):
        assert f"\n- `{field}`" in readme, field


def test_the_readmes_keep_the_two_pull_requests_apart(tmp_path: Path) -> None:
    """An order has its own pull request and names the reviewed one.
    A bare "the pull request" leaves the reader to guess which."""
    root = _render_waybill(tmp_path)
    for relpath in ("README.md", "dispositions/README.md"):
        text = (root / relpath).read_text().replace("\n", " ")
        # The owner comment says which one in the same breath.
        text = text.replace("the pull request whose commits wrote the line", "")
        # Decision 49's own words, qualified in the same breath.
        text = text.replace("the pull request it worked on", "")
        assert "the pull request" not in text, relpath


def test_the_dispositions_readme_is_for_reviews_outside_a_chain(tmp_path: Path) -> None:
    """Inside a chain the results carry findings and answers; a disposition file is for the rest."""
    text = (_render_waybill(tmp_path) / "dispositions" / "README.md").read_text()

    assert "outside a chain" in text
    assert "no disposition file is written" in text


def test_the_readme_describes_the_result_and_its_check(tmp_path: Path) -> None:
    """The ruled design of 2026-11-17: a JSON result written twice by the driver,
    and a check that opens the next order and never merges one."""
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "## The result\n" in readme
    assert "## The result check\n" in readme
    assert "never merged" in readme
    assert "## Not built yet\n" in readme


def test_the_order_check_pins_its_actions_to_commits(tmp_path: Path) -> None:
    check = (
        _render_waybill(tmp_path) / ".github" / "workflows" / "order-check.yml"
    ).read_text()

    uses = re.findall(r"uses: (\S+)", check)
    assert uses
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", use) for use in uses), uses


def test_every_order_names_a_model_tier(tmp_path: Path) -> None:
    """Each Routine serves one tier, so an order without a tier would start no session."""
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "Every order sets `model_tier`" in readme
    assert "must set neither" not in readme


def test_a_worker_changes_only_the_result_file_in_this_repository(
    tmp_path: Path,
) -> None:
    """An implementer commits code, so the limit holds for the waybill only."""
    root = _render_waybill(tmp_path)
    readme = (root / "README.md").read_text()
    rules = (root / "AGENTS.md").read_text()

    assert "change only the result file" not in rules
    assert "pushes change no other file" not in readme
    assert "in this repository it changes only the result file" in readme


def test_the_disposition_file_term_links_the_shared_glossary(tmp_path: Path) -> None:
    readme = (_render_waybill(tmp_path) / "README.md").read_text()

    assert "template/docs/glossary/disposition-file.md" in readme
    assert "with its owner and its status" in readme


def test_the_agent_rules_keep_the_waybill_one_way(tmp_path: Path) -> None:
    """Orders may pin Pandoscope repositories read-only.
    Nothing of the org's own work is written into a Pandoscope repository."""
    rules = (_render_waybill(tmp_path) / "AGENTS.md").read_text()

    assert "one way" in rules.lower()
    assert "token, secret, session transcript" in rules


def test_every_order_is_checked_before_a_session_reads_it(tmp_path: Path) -> None:
    check = (
        _render_waybill(tmp_path) / ".github" / "workflows" / "order-check.yml"
    ).read_text()

    assert 'paths: ["orders/*.yml"]' in check
    assert "order check" in check


def test_the_readme_is_vendored(tmp_path: Path) -> None:
    """What a waybill IS belongs to the template.
    The next update replaces any edit to one org's waybill, so the contract does not fork."""
    dst_path = _render_waybill(tmp_path)
    (dst_path / "README.md").write_text("# Edited in the waybill\n")

    _recopy(dst_path)

    assert (dst_path / "README.md").read_text() == (
        SUBTEMPLATE / "README.md"
    ).read_text()


def test_the_shortcodes_are_seeded_once(tmp_path: Path) -> None:
    """`shortcodes.json` is the org's own list.
    A recopy that clobbered it would silently rename every order the org places."""
    dst_path = _render_waybill(tmp_path)
    org_owned = '{"my-org/my-repo": "mr"}\n'
    (dst_path / "shortcodes.json").write_text(org_owned)

    _recopy(dst_path)

    assert (dst_path / "shortcodes.json").read_text() == org_owned
