# Waybill

This repository carries the orders that start worker sessions, and the results those sessions write.
An order is an order file on a branch `order/<name>` of this repository, opened as a pull request.
Opening that pull request makes a Routine start a worker session,
and the worker reads the order file from its checkout of the branch.

Orders come in chains.
Pando or the principal places the first order of a chain.
Every order after it is a step of the chain: the result check opens it when the order before it has a complete result.
Nobody places those orders, and a worker never opens or closes an order itself.

Two kinds of pull request are involved:

- the order's pull request, in this repository, which carries the order file and the result;
- the reviewed pull request, in the repository the work is for, which a review order names.

## Terms

- **Principal:** the person who owns this org's agents and approves their work.
- **Pando:** the orchestrator, which organizes the other sessions.
  An orchestrator session counts as Pando.
- **Forge:** the service that hosts the repositories and their pull requests, such as GitHub.
- **Pandoscope:** the software factory whose `pandoscope` tool sets up a project as a forge org and orchestrates its agent sessions.
- **Harness:** the environment that gives a model tools, files and a session, such as Claude Code.
  Some harnesses can also start a session through a webhook when an event occurs, such as a pull request opened on the forge.
- **Routine:** a routine (taking Claude Code on the web as an example here) is a configuration of a certain harness environment.
  It installs a webhook that listens for an event (such as a pull request opened in this repository)
  and starts a session with the configured environment.
- **Worker:** the agent, a model in the harness, with a worker role assigned, such as reviewer or implementer.
  The role decides whether the model may write or only reads and reviews.
  A worker has one task and keeps no threads.
- **Driver:** the hooks that the Routine's session installs.
  The worker works until the driver's checks pass.
- **Composer:** the Pandoscope command that writes a session's task from the order.
- **Ticket:** an issue on the forge, written `org/repo#n`.
- **Pass:** one kind of review.
  The pass `spec-fidelity` checks that the reviewed pull request does what its tickets ask;
  the pass `prose` checks its text against the writing rules.
- **Model tier:** the family of models that runs a session, such as `opus`.
- **Result:** the JSON file that a worker's session leaves on the order's branch.
- **Finding:** one fault that a review reports in its result, with its file and line.
- **Disposition file:** the answers to the findings of one review that ran outside a chain, one entry per finding.

## An order

Every order has a unique name.
The order is stored in `orders/<name>.yml` and committed on a branch named `order/<name>`.

The first word of the name says what kind of order it is.
The name of a review order follows this pattern:

`review-<pass>-<model_tier>-<code><n>`

- `pass`: the kind of review, such as `spec-fidelity` or `prose`.
  One pull request can get several passes, and each pass has its own order.
- `model_tier`: the model tier that runs the session, such as `opus`.
- `code`: the reviewed repository's shortcode, as listed in [shortcodes.json](shortcodes.json).
- `n`: the number of the reviewed pull request.
  The name needs the shortcode too, because each repository numbers its pull requests from 1.

For example, `review-spec-fidelity-opus-mr4` orders a spec-fidelity review of `my-org/my-repo#4` on the `opus` tier.

Each Routine picks its orders by branch name.
An order whose name is off the pattern, or names a tier without a Routine, would start no session,
so the order check fails such an order.

## The order file

```yaml
id: review-spec-fidelity-opus-mr4
role: reviewer
pass: spec-fidelity
model_tier: opus
pull_request: my-org/my-repo#4
checkouts:
  pandoscope/skills: main
tickets:
  - my-org/my-repo#3
```

- `id`: the order's name.
  It must equal the file's name without `.yml`, or `pandoscope order check` fails.
- `role`: the worker role, such as `reviewer` or `implementer`.
  An order sets a worker role only; only the principal sets the role orchestrator.
- `pass` and `model_tier`: the same two values as in the name.
  A `reviewer` order must set both, and an order of any other role must set neither.
- `pull_request`: the reviewed pull request, written `org/repo#n`.
  A review order sets it.
  An implementer order sets none, because the implementer has no pull request when it starts.
  The composer fetches the head of the reviewed pull request, so its repository needs no entry under `checkouts`.
- `base`, optional: the branch that the reviewed pull request targets.
  Set it when that branch is not the repository's default branch, as in a stack of pull requests.
- `checkouts`, optional: one line `org/repo: ref` for each repository that the session must read at a ref other than its default branch.
  The lines are not nested.
  A session-start hook switches the clone of each listed repository to its ref before the composer runs.
  The hook never switches the clone of this repository, which stays at the head of the order's pull request.
- `tickets`: for a review, the tickets that the reviewed pull request implements, which a review reads as its specification;
  for an implementer, the ticket to implement.
  The driver does not let a session end before it has read every listed ticket.
- `result`: not used.
  The result is a file on the order's branch, described below.

### A prose review order

A prose review order sets `pass: prose`, and its branch is `order/review-prose-<model_tier>-<code><n>`.
It also lists `pandoscope/skills` under `checkouts`, at a ref that holds the folder `original/writing-prose/rules/`.
The reviewer reads the writing rules from that folder.
The branch `main` holds it.

## The result

The result is a JSON file that a schema defines.
The driver writes it in two steps, and a session's pushes change no other file:

1. At session start, before the worker gets control, a script records the session's setup:
   the session id, the role, and the ref of each checkout, with no value that must not leave the session.
   The driver writes that as the result, marks it incomplete and pushes it at once.
2. At the end of every session, whatever the outcome, the driver rewrites the result with the session's end state and pushes it as its last act.

What a result holds besides that depends on the role:

- A review's findings live in its result; a reviewer pushes nothing to the reviewed repository.
- An implementer reads the results on its order's branch and marks each finding as addressed, with its fixup or new commit,
  or declines it with a reason.
- A reviewer of the same pass first checks the findings it raised before.
  It signs off each one that is addressed or reasonably declined, and carries the rest into its new result.
- A worker that discovers a fault outside its task looks for a ticket.
  It names the ticket in its result if one exists, and otherwise records the discovery there.
  A worker never writes to the tracker.

## The result check

A check on the order's pull request reads every result the driver pushes.

- An incomplete result: the check does nothing.
- A result that does not parse or is incomplete at the end of a session: the check fails and names what is missing.
  The worker fixes it; if that fails, the principal or the orchestrator that placed the chain is told.
- A complete result: the check validates it against the schema and checks that
  the result's order is the branch's order,
  the commit the worker read is still the head of the branch it worked on,
  the push changed only the result file,
  every ticket of the order is listed as read,
  every file and commit the result points to exists,
  the commit is signed by the identity that runs workers,
  and the outgoing scan finds no blocked term.
  More checks are added as the work shows the need.
- When the result passes: the check opens the chain's next order on a new branch that starts from this order's branch,
  closes this order's pull request without merging and deletes this order's branch.
  The newest order's branch therefore holds every earlier order and result as its history.

A review and an implementer alternate until every finding is signed off, and then the chain goes on.
A finding that is not signed off after three rounds makes the order stuck.

## The Routines

The principal saves one Routine for each model tier.
Everything else comes from the order: the role, the pass, the reviewed pull request and the checkouts.
The environment cannot come from the order, so it stays with the Routine:
the model and the repositories attached.

Each Routine has a filter on the branch of the order's pull request.
The Routine for the `opus` tier has the filter `order/review-[0-9a-z-]+-opus-.*`,
so it starts a session for the example order above.

Two duties are the principal's:

- Before a Routine starts its first session, the principal checks that the Routine runs a model of its tier.
- The principal attaches to each Routine every repository that an order may need,
  because a session cannot clone a private repository it was not started with.
  A session in a cloud environment removes the clones its task does not need.

## What the harness hands the session

The harness checks out the head of the order's pull request.
It describes that pull request in the environment variables `CCR_TRIGGER_REPO`, `CCR_TRIGGER_PR_NUMBER`,
`CCR_TRIGGER_HEAD_REF`, `CCR_TRIGGER_HEAD_SHA` and `CCR_TRIGGER_BASE_REF`.
The session's own order is the file `orders/<name>.yml` for the branch `order/<name>` in `CCR_TRIGGER_HEAD_REF`.

## The lifecycle of an order

An order's pull request is a message to the Routine, and it is never merged.
The branch `main` therefore holds no order files.

- **Open:** the order's pull request is open, and its result is incomplete or failing.
- **Done:** its result passed, the next order of the chain is open, and its pull request is closed and its branch deleted.
- **Last:** the chain has no next order.
  Pando and the principal verify that the flow was correct and review the discoveries of all its results.
  The principal then closes the last order's pull request and deletes its branch.
  What the principal approves is the reviewed and finished work, in its pull request in the repository it belongs to, never an order.
- **Stuck:** a finding is not signed off after three rounds,
  or the worker has written nothing to the thread ledger for an hour and no final result is on the order's branch.
  The principal is told at once, one order at a time.
  A session that dies without pushing its final result is a defect of the chain.
- **Abandoned:** Pando or the principal closes an order placed by mistake.
  No order is closed or deleted silently.

## Not built yet

This README states the design that the principal ruled on 2026-11-17.
These parts do not exist yet:

- the result schema, the result check, and the order schema's changes for implementer orders, worker roles and names:
  pandoscope/pandoscope#54 and agentic-engineering-template#330;
- the driver's two writes of the result: pandoscope/skills#278.

Until they exist, a review driver pushes its findings to a branch of the reviewed repository,
the findings are answered in a disposition file,
and the session that placed an order closes it once the disposition file is written.

## Dispositions

A review that runs outside a chain has no result on an order's branch.
Its findings are answered in this repository, in a disposition file `dispositions/<name>.yml`,
and not in comments on the forge.
That keeps the record in git, so it does not depend on which forge hosts the repositories.
[dispositions/README.md](dispositions/README.md) describes the format.

## One way only

An order may list a Pandoscope repository under `checkouts`, such as `pandoscope/skills`, which holds the rules that a review reads.
A session only reads such a clone.

Nothing goes the other way.
No session writes one of this org's orders, results, findings or disposition files, or the name of one of this org's repositories, into a Pandoscope repository.
Pandoscope's repositories serve every org that Pandoscope sets up, so they carry no single org's work.
This is a rule that every session follows; nothing in this repository can enforce it.
