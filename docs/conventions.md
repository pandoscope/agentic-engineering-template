# Agentic Engineering Template — Project Conventions

Repo-specific rules referenced from [AGENTS.md](../AGENTS.md). This file is
seeded once by the agentic template and never overwritten by `copier update` —
edit it freely.

## Template-First Changes (Self-Application)

This repo is the template AND uses itself as a template: root artifacts that
have a counterpart under `template/` (`AGENTS.md`, `CLAUDE.md`,
`skills-lock.json`, `scripts/doctor.sh`, `scripts/agent-shims/`, …) are
template *output*, never hand-edited. Editing both places duplicates work;
editing only the root drifts from the template. When unsure whether a root
file is templated, check for its counterpart under `template/` (e.g.
`template/CLAUDE.md.jinja`) before editing.

For any change touching a templated file, always follow this route:

1. Edit only the source under `template/` (plus `copier.yml`, `extensions/`,
   tests).
2. Commit the template-only change(s).
3. Self-apply: render the template from the current branch and adopt the
   rendered output for the affected root files verbatim:

   ```bash
   uv run copier copy --trust --defaults --skip-tasks --vcs-ref HEAD \
     --data agentic_project_name="Agentic Engineering Template" \
     --data agentic_project_description="Copier template for agentic engineering scaffolding" \
     --data agentic_project_slug=agentic-engineering-template \
     --data agentic_repo_owner=frankify-app \
     --data agentic_merge_approvers=pando-genet \
     . <tmp-render-dir>
   ```

4. Copy the affected files from the render into the repo root and commit them
   as a separate `chore: reapply template to self` commit.

This doubles as a proving ground: the self-applied root files are the rendered
template output, reviewed in the same PR as the template change.

The route is enforced, not merely conventional: a commit that edits a template
source and the root file it stamps is rejected, by the `self-application-route`
prek hook at commit time and by the `Template-first route (per commit)` CI job
over every commit a PR adds. State cannot catch this — a hand-edit that happens
to match the render leaves a valid tree — so provenance is checked where the
information still exists, at the commit boundary.

Exception: root files that intentionally diverge from the template are NOT
overwritten by self-application. The authoritative list is
`DELIBERATE_DIVERGENCE` in `scripts/dev/self_application.py`, read by both the
state check (`tests/test_self_application.py`, which fails the build when the
root and the render disagree anywhere else) and the route guard — so adding a
template file forces a decision: adopt it at root, or list it with a reason.
Paths in copier's `_skip_if_exists` are excluded structurally (they are seeded
once and can never match) rather than listed as choices.

Step 3 is not optional. The shared glossary terms live under
`template/docs/glossary/`, which is never a glossary root, so they become real
terms only once stamped into this repo's own `docs/glossary/`. A stale stamp
means the glossary being linted is not the glossary being shipped.
