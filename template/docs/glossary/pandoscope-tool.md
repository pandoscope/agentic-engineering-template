## Pandoscope tool

<!-- d10e: auto-prune -->
<!-- Copier-vendored from the agentic-engineering-template — do NOT edit
     here; change it in the template and pull via `copier update`. The
     auto-prune marker above lets `disambiguate prune` remove this term
     from a repo that never links it. -->

[Pandoscope](pandoscope.md) uses its own process to build and publish a
tool as the `pandoscope` package, providing the `pandoscope` command.
This tool can create a GitHub [organization](org.md) and
[stamp](template-stamp.md) its repository from Pandoscope's
[templates](pandoscope-template.md). It provides a composer that tailors
the context to a [session](agent-session.md)'s role (such as
orchestrator, implementer, or reviewer) and constructs an autonomous
software engineering pipeline where [worker](pando-worker.md) agents can
turn a ticket into a refined implementation that is ready to merge.

_Avoid_: Pandoscope, for the tool
