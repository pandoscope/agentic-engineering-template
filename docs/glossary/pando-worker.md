## Pando worker

<!-- d10e: auto-prune -->
<!-- Copier-vendored from the agentic-engineering-template — do NOT edit
     here; change it in the template and pull via `copier update`. The
     auto-prune marker above lets `disambiguate prune` remove this term
     from a repo that never links it. -->

A **Pando worker** is an [agent](agent.md), a
[model](large-language-model.md) in a [harness](harness.md), with a
worker role assigned, such as reviewer or implementer. The role decides
whether the model may write or only reads and reviews. A worker starts
only from a waybill order: [Pando](pando.md) starts one by creating an
order, and a workflow can start one mechanically, as the next order of
a chain. A worker has one task and keeps no threads: it reports how far
along it is and whether it ran into a problem, and it writes to the
thread ledger at least once an hour while it works. It works until the
checks of the [Pando driver](pando-driver.md) pass. Every worker has a
role, but not everything with a role is a worker: Pando has the
orchestrator role, talks with the [principal](principal.md), and is not
a worker. Each task gets a new worker with a fresh context, so the
worker starts unbiased.

_Avoid_: subagent
