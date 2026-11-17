## Pandoscope tool

<!-- d10e: auto-prune -->
<!-- Copier-vendored from the agentic-engineering-template — do NOT edit
     here; change it in the template and pull via `copier update`. The
     auto-prune marker above lets `disambiguate prune` remove this term
     from a repo that never links it. -->

The **Pandoscope tool** is the `pandoscope` command that
[Pandoscope](pandoscope.md) builds with its own process.
`pandoscope stamp` renders the template into a project's repository.
`pandoscope compose`, the composer, runs when a session starts: it
composes the session's instructions from its role and, where a waybill
order exists, its task from the order. `pandoscope order check`
validates waybill order files, and the waybill's pull request checks
run it.

_Avoid_: Pandoscope, for the tool
