## Pando worker

<!-- d10e: auto-prune -->
<!-- Copier-vendored from the agentic-engineering-template — do NOT edit
     here; change it in the template and pull via `copier update`. The
     auto-prune marker above lets `disambiguate prune` remove this term
     from a repo that never links it. -->

A **Pando worker** is an [agent session](agent-session.md) that works
one task autonomously. [Pando](pando.md) spawns workers, and a
workflow can start one the same way: mechanically, as the next step
after a role's last pass has decided whether changes are needed. A
worker has a role, such as reviewer or implementer, and the role
decides how the worker performs its task: a reviewer, for example,
does not change the code. Every worker has a role, but not everything
with a role is a worker: Pando itself has a role, talks with the
[principal](principal.md), and is not a worker. Each task gets a new
worker with a fresh context, so the worker starts unbiased.

_Avoid_: subagent
