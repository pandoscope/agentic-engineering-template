# agentic-template

[![Copier](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/copier-org/copier/master/img/badge/badge-grayscale-inverted-border-orange.json)](https://github.com/copier-org/copier)

A [Copier](https://copier.readthedocs.io/) template that overlays agentic-engineering conventions onto any repository — standalone or layered on top of another Copier template (for example [browniebroke/pypackage-template](https://github.com/browniebroke/pypackage-template)).

## What it ships

Generated projects receive:

- **Agent docs** — `AGENTS.md` and `CLAUDE.md`, templated with project name, description, slug, and (optionally) forge/repo URL. `AGENTS.md` keeps a universal core (glossary, skills, git/tracker workflow, style); its coding sections render only when `agentic_project_kind` is `code`. It links to `docs/conventions.md`, a repo-owned file seeded once and never overwritten on `copier update` — rich local rules go there without conflicts.
- **Skills** — pinned in `skills-lock.json`; a post-render step runs `npx skills@latest experimental_install` to populate `.agents/skills/`. Code-only skills (`tdd`, `requesting-code-review`, `to-tickets`) are included only when `agentic_project_kind` is `code`. `.claude/skills` is shipped as a symlink to `.agents/skills` so Claude Code registers the installed skills as platform skills / slash commands (`experimental_install` doesn't propagate into per-agent directories on lockfile restore). Windows note: git symlinks need `core.symlinks=true` plus developer mode.
- **Glossary** — `docs/glossary/` with a seed entry for the project itself. Terms are resolved with `uvx disambiguate==<agentic_disambiguate_version>` (pinned — disambiguate is pre-alpha with breaking changes between releases).
- **Architecture stub** — `docs/architecture.md` linking to the project glossary term (`agentic_project_kind` is `code` only).
- **Cross-cutting lint hooks** (optional) — when `agentic_precommit` is `prek`, a `.pre-commit-config.yaml` covering commit messages, JSON, Markdown, glossary lint (pinned `disambiguate --lint` as a local hook — no repo-local `core.hooksPath` setup needed; lint roots are configurable via the `agentic_disambiguate_roots` answer so repos never patch the rendered hook), and — for English content only — spelling, and file cost (a local hook capping everything a model reads — source, Markdown, YAML, TOML, JSON, HTML, CSS — at `agentic_max_file_tokens` estimated tokens — bytes/4 — plus code-line limits: 500 for Python/JS/TS, 150 for bash, comments free; per-file exemptions in `.file-length-allowlist` each name a ticket). No unit tests in prek — tests belong in CI. Language-specific linting stays with whatever template owns the source code.
- **Shared config** — `.editorconfig`, `.codespellrc` (English content only), `commitlint.config.mjs`, and `scripts/doctor.sh` (host-tool checks).
- **Scheduled template updates** (GitHub forge only) — `.github/workflows/template-update.yml` runs `copier update` weekly and opens a PR when a newer template release exists. See [Automated template updates](#automated-template-updates).
- **Lint CI** (GitHub forge only) — `.github/workflows/lint.yml` fails on committed copier conflict markers (always rendered, even with `agentic_precommit` set to `none` — it guards the updater contract) and, when `agentic_precommit` is `prek`, runs all prek hooks over all files. CI evaluates the tree at PR HEAD while prek runs per commit locally, so the workflow complements the local hooks rather than replacing them — intermediate commits (e.g. TDD red-step commits) stay lintable locally without blocking the PR.

All Copier questions use the `agentic_` prefix so this template can layer without colliding with other templates. Answers are stored in `.copier-answers.agentic.yml` (not Copier's default).

## Prerequisites

**To run `copier copy` / `copier update` on this template**, install [copier-template-extensions](https://github.com/copier-org/copier-template-extensions) alongside Copier (loads template-local Jinja helpers for forge/owner defaults):

```shell
pip install copier copier-template-extensions
# or, in this repo: uv sync
```

Host tools checked by `scripts/doctor.sh` (see [Tooling](#tooling)):

| Tool | When required |
| --- | --- |
| `git` | always |
| `npx` | always (skills) |
| `uvx` | always (disambiguate / glossary) |
| `gh` | always (used by `ghx`; overridden in agent PATH) |
| `ghx` | deferred — doctor prints a warning only |
| `prek` | when `agentic_precommit` is `prek` |

Run `scripts/doctor.sh --install` to bootstrap missing host CLIs via your platform package manager (`brew` on macOS, `apt` on Debian/Ubuntu). Doctor never installs project dependencies (`uv sync`, `npm install`, etc.).

## Standalone usage

Generate agentic scaffolding into a new or existing directory:

```shell
copier copy --trust <path-or-url-to-this-template> path-to-project
```

Use `--defaults` to accept defaults without prompts. Copier writes answers to `.copier-answers.agentic.yml` and renders files from the `template/` subdirectory.

Refresh after template updates:

```shell
copier update --answers-file .copier-answers.agentic.yml --trust
```

## Layering on another template

Apply a language or domain template first, then overlay agentic conventions with a separate answers file:

```shell
# 1. Base project (example: Python package)
copier copy --trust gh:browniebroke/pypackage-template my-project
cd my-project

# 2. Agentic overlay (same target directory)
copier copy --trust <path-or-url-to-agentic-template> .

# 3. Refresh only the agentic layer later
copier update --answers-file .copier-answers.agentic.yml --trust
```

Each template keeps its own answers file. The base template retains `.copier-answers.yml` (or whatever it defines); this template always uses `.copier-answers.agentic.yml`.

### Choosing `agentic_precommit`

| Value | Effect |
| --- | --- |
| `prek` | Writes `.pre-commit-config.yaml` with cross-cutting hooks (commitlint, JSON, Markdown, codespell). No unit-test hooks. |
| `none` | Does **not** write `.pre-commit-config.yaml`. Use when the base template already owns pre-commit, or you manage hooks yourself. |

When layering on pypackage-template, set `agentic_precommit` to `none` if you want pypackage's language hooks to remain the sole `.pre-commit-config.yaml` owner.

## File-ownership policy

This template only writes files it is configured to own. It does not silently take over files owned by a layered template.

| File / area | Owner |
| --- | --- |
| `AGENTS.md`, `CLAUDE.md` | agentic-template |
| `docs/conventions.md` | **project** — seeded once by agentic-template, never overwritten (`_skip_if_exists`) |
| `skills-lock.json`, `.agents/skills/`, `.claude/skills` (symlink) | agentic-template |
| `docs/glossary/`, `docs/architecture.md` | agentic-template |
| `.editorconfig`, `.codespellrc`, `commitlint.config.mjs` | agentic-template |
| `scripts/doctor.sh` | agentic-template |
| `.github/workflows/template-update.yml`, `.github/workflows/lint.yml` | agentic-template (GitHub forge only) |
| `.pre-commit-config.yaml` | agentic-template **only** when `agentic_precommit` is `prek`; otherwise not written |
| `.file-length-allowlist` | seeded once by agentic-template when `agentic_precommit` is `prek`, then owned by the repo |
| Source code, package manifests, language lint hooks | base / layered template |

**Conflict detection** is not enforced inside `copier.yml`. Copier's default update behaviour may produce `.rej` files and still exit zero. CI on this repository runs `copier update` against fixture projects and fails if any `.rej` files exist or the tree is unexpectedly dirty — surfacing collisions (for example both templates touching `.pre-commit-config.yaml`) instead of letting silent rejects rot.

## Automated template updates

On the GitHub forge, generated projects receive `.github/workflows/template-update.yml`. The workflow is self-propagating: update a consuming repo once and it keeps receiving updater changes with every subsequent template release.

What it does:

- **Trigger** — weekly cron plus manual `workflow_dispatch`.
- **Update** — runs `copier update --defaults --trust --skip-tasks` against the latest template release tag (tasks are skipped because post-render tasks need host tools absent on CI runners).
- **No changes** — exits clean, no PR.
- **Changes** — pushes a `chore/template-update-<version>` branch and opens a PR whose body shows the version delta and the release changelog excerpt. If an update PR is already open, the run skips instead of stacking duplicates.
- **Conflicts** — copier's inline conflict markers are committed as-is; the PR still opens and red lint/CI flags the markers for human/agent resolution on the branch. Updates never silently stall.

### Required setup in consuming repos

The workflow authenticates with a GitHub App installation token (same pattern as a semantic-release bot) instead of the default `GITHUB_TOKEN` — PRs opened with `GITHUB_TOKEN` trigger no CI, which would defeat the conflict-surfacing strategy. Each consuming repo needs:

1. A GitHub App (e.g. your release bot) installed on the repository with **contents: read/write** and **pull requests: read/write** permissions.
2. A repository (or org) **variable** `RELEASE_BOT_CLIENT_ID` set to the app's client ID.
3. A repository (or org) **secret** `RELEASE_BOT_PRIVATE_KEY` containing an app private key.

Until these are configured, scheduled runs fail at the token-minting step and do nothing else.

## Configuration

Questions asked at generation time (all prefixed `agentic_`):

| Variable | Description |
| --- | --- |
| `agentic_project_name` | Human-readable project name (agent docs, glossary). |
| `agentic_project_description` | Short description (glossary seed entry). |
| `agentic_project_slug` | Kebab-case slug (paths, repo URL). Auto-derived from `agentic_project_name`; override if needed. |
| `agentic_project_kind` | `code` or `docs`. Single gate for all code-specific artifacts (AGENTS.md coding sections, code-only skills, `docs/architecture.md`). Flip the answer and run `copier update` when code lands. |
| `agentic_language` | Primary content language (ISO 639-1, default `en`). codespell renders only for English content; future language-sensitive tooling gates on the same answer. |
| `agentic_disambiguate_version` | Pinned disambiguate version used by the prek glossary-lint hook and the `uvx disambiguate==…` commands in AGENTS.md. |
| `agentic_precommit` | `prek` or `none`. |
| `agentic_max_file_tokens` | Estimated tokens (file bytes / 4) a file may cost before the file-length hook fails (default `10000`) — token cost is what an agent pays to read it, so the hook covers prose and config alongside source (lockfiles and minified assets excluded). Code files also get code-line limits, comments free: 500 for Python/JS/TS (split it), 150 for bash (long bash means rewrite in Python). Existing overruns go on `.file-length-allowlist`, one per line with the ticket that will bring each back under; the list is seeded once and owned by the repo. |
| `agentic_forge` | `github`, `forgejo`. Auto-defaults to `github` when the git remote contains `github.com`. |
| `agentic_forgejo_host` | Forgejo hostname; asked only when `agentic_forge` is `forgejo`. |
| `agentic_repo_owner` | Repository owner / org. Defaults from `gh api user` or `git config github.user`; fails if neither resolves. |
| `agentic_repo_host` | Derived — not asked. `github.com`, the Forgejo host, or unset. |

## Tooling

`scripts/doctor.sh` is the single source of truth for required host tools. It supports two modes:

- **Check** (default) — print a ✓/✗ report, exit non-zero if anything required is missing. Used by post-render tasks and safe to run any time.
- **Install** (`--install` / `--fix`) — opt-in bootstrap of missing host CLIs only.

Post-render tasks call doctor in check mode only — never auto-install.

## Glossary

Terms this template defines. The shared set under
[`template/docs/glossary/`](template/docs/glossary/) is stamped into every
generated repo; the copies below are this repo's own stamped output, which is
what makes them lintable here.

- [agentic-engineering-template](docs/glossary/agentic-engineering-template.md)
- [decision-memory](docs/glossary/decision-memory.md)
- [decision record](docs/glossary/decision-record.md)
- [evidence-memory](docs/glossary/evidence-memory.md)
- [org genome](docs/glossary/org-genome.md)
- [grilling](docs/glossary/grilling.md)
- [memory repo](docs/glossary/memory-repo.md)
- [org](docs/glossary/org.md)
- [pando](docs/glossary/pando.md)
- [pando cell](docs/glossary/pando-cell.md)
- [preference set](docs/glossary/preference-set.md)
- [principal](docs/glossary/principal.md)
- [record contract](docs/glossary/record-contract.md)
- [reinset](docs/glossary/reinset.md)
- [agent session](docs/glossary/agent-session.md)
- [session-memory](docs/glossary/session-memory.md)
- [template stamp](docs/glossary/template-stamp.md)
- [pandoscope template](docs/glossary/pandoscope-template.md)

These links live in this repo's own README, which is NOT part of the render —
so in a generated repo the same terms arrive unlinked and are pruned
(`disambiguate prune`) by the post-stamp task, and again by the rendered
template-update and lint workflows, which run copier with `--skip-tasks`.
A consumer keeps exactly the shared terms it links; nothing roots them by
hand. One script does it everywhere, `scripts/ci/prune_glossary.sh`: the
post-stamp task, the update workflow and the drift job run it plain, and this
root runs it on every commit as the `prune-glossary` prek hook with
`--fail-on-removal` — here every term must survive, because the glossary is
render output pinned by the self-application test, so a removal is a defect
and the hook fails naming and restoring the term.

## Developing this template

```shell
uv sync
uv run pytest
```

Tests generate projects into temporary directories via Copier's Python API and assert conditional files (for example `.pre-commit-config.yaml` absent when `agentic_precommit` is `none`).

A smoke test (`test_e2e_smoke_full_render_runs_tasks`) exercises the full generation path — including the post-render `_tasks` step — and asserts a clean tree. It skips automatically when `git`, `npx`, or `uvx` are not on `PATH`.
