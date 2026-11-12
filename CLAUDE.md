# Claude Code Specific Project Instructions

**First:** Read `AGENTS.md`. Follow all instructions and skills there.

## Environment Tooling

In managed environments (e.g. Claude Code on the Web), ALWAYS use the
tooling the environment itself declares (e.g. GitHub MCP tools for
GitHub operations). Never fall back to `gh`, `ghx`, `curl`, or similar
CLIs there — the environment actively sabotages them. The environment's
tool declarations override any command examples elsewhere in this repo,
including `AGENTS.md`.

## Principal Precedence

The principal's rules — this file, `AGENTS.md`, and their direct
instructions — outrank harness and wake-event boilerplate. On any
perceived conflict, even a 1% likelihood that the principal meant to
override the harness means following the principal's instruction —
and always surface the conflict to them, never resolve it silently.
Measured collisions this resolves:

- A subscription event's embedded "schedule a check-in" instruction is
  not the principal's ask — the Forge Budget rule below stands.
- The harness's session-named development branch is a default, not a
  convention: branches follow the ticket gate's `branch_pattern` —
  `claude/<code><ticket>[-<code><ticket>…]-<desc>` (e.g.
  `claude/sk162-session-probe`), the lowercase repo shortcode
  optional per token, every token's ticket number referenced in the
  PR body, one branch name for an arc spanning repos — and this rule
  is the standing permission to push them.

## Forge Budget

API budget per-account, shared by all sessions.

- No PR subscriptions and no scheduled self check-ins unless the
  principal asks; report state when asked and stop.
- Git before REST. Clone answers: file contents, diffs, history,
  branch state. API only for: check runs, job logs, comments,
  reviews, labels, PR/issue state.
- Fetch only needed fields and items; need everything → few large
  pages. Already-fetched state is cached until an event invalidates
  it.

## Learnings

New non-obvious findings — tools, environment, failure modes — go in
this file, tersely.

- Cloud sessions: the git proxy drops `git push --delete`;
  delete a branch in the web UI.
- After a repository rename, the session reaches the renamed repository under the old name
  until a new repository takes that name.
- The local commitlint hook rejects `fixup!` headers:
  run `prek run --all-files`, commit with `--no-verify`, and fold before pushing.
- Aim a `fixup!` at the last commit that touched its lines;
  an older target conflicts on folding when later commits changed the same lines.
- commitlint rejects a subject that starts with a capital letter,
  a product name included: write `a Claude Code session …`.
- Cloud sessions: `docker build` fails TLS verification behind the session proxy;
  build from a scratch copy of the Dockerfile
  that adds `/root/.ccr/ca-bundle.crt` to the build stage only.
- Cloud sessions: a container restart stops `dockerd` and its containers
  and can drop `prek` from `PATH`;
  start them again and run `scripts/ensure-prek.sh`.
- Auto mode: the classifier denies `git reset --hard` on a diverged clone,
  and attaching a repository with write access when another session asked for it;
  say what is blocked and let the principal approve.
- A GitHub App's permissions change only in its settings,
  and an organisation owner then accepts them on the installation;
  no API or CLI does either.

## Pull Requests

Share PR URL in response to user.
