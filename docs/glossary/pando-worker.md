## Pando worker

<!-- d10e: auto-prune -->
<!-- Copier-vendored from the agentic-engineering-template — do NOT edit
     here; change it in the template and pull via `copier update`. The
     auto-prune marker above lets `disambiguate prune` remove this term
     from a repo that never links it. -->

An [agent session](agent-session.md) that [Pando](pando.md) spawns to
work one task without a conversation. A worker has a role, such as
reviewer or implementer, and the task decides it. Every worker has a
role, but not every role is a worker: Pando itself talks with the
[principal](principal.md) and is not one. Workers are disposable; the
[org](org.md) persists.

_Avoid_: subagent
