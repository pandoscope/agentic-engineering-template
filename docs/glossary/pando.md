## Pando

<!-- d10e: auto-prune -->
<!-- Copier-vendored from the agentic-engineering-template — do NOT edit
     here; change it in the template and pull via `copier update`. The
     auto-prune marker above lets `disambiguate prune` remove this term
     from a repo that never links it. -->

**Pando** is the orchestrator [agent](agent.md): the
[principal](principal.md)'s single interface to an [org](org.md), like a
ship's First Mate. Every orchestrator session counts as Pando, and the
principal may run several at once. Only the principal sets the
orchestrator role; a waybill order or an environment sets only a worker
role. Pando starts workers only by creating waybill
orders.
