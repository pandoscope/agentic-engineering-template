# Waybill

This repository carries the orders that start worker sessions, and the results those sessions write.
An order is an order file on a branch `order/<name>` of this repository, opened as a pull request.
Opening that pull request makes a Routine start a worker session,
and the worker reads the order file from its checkout of the branch.

Orders come in chains.
Pando or the principal places the first order of a chain.
Every order after it is a step of the chain: the result check opens it when the order before it has a complete result.
Nobody places those orders, and a worker never opens or closes an order itself.

The order's pull request and the reviewed pull request are different pull requests:

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
- **Worker:** a model in the harness that holds a worker role, such as reviewer or implementer.
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
- **Result:** the JSON file on the order's branch that the driver creates and the worker fills.
- **Finding:** one fault that a review reports in its result, with its file and line.
- **[Disposition file](https://github.com/pandoscope/agentic-engineering-template/blob/main/template/docs/glossary/disposition-file.md):** the answers to the findings of one review that ran outside a chain,
  one entry per finding, with its owner and its status.

## An order

Every order has a unique name.
The order is stored in `orders/<name>.yml` and committed on a branch named `order/<name>`.

The first word of the name says what kind of order it is.
The name of a review order follows this pattern:

`review-<pass>-<model_tier>-<code><n>-<step>`

The name of an implementer order follows this pattern:

`implement-<model_tier>-<code><n>-<step>`

- `pass`: the kind of review, such as `spec-fidelity` or `prose`.
  One pull request can get several passes, and each pass has its own order.
- `model_tier`: the model tier that runs the session, such as `opus`.
- `code`: the shortcode of the subject's repository, as listed in [shortcodes.json](shortcodes.json).
- `n`: the number of the order's subject, an issue or pull request.
  The name needs the shortcode too, because each repository numbers its issues and pull requests from 1.
- `step`: the number of the order's step in its chain, counted from 1.
  Orders that run side by side in one step share its number and differ in pass or tier.
  A repeat run of the same pass and tier in the same step takes a letter after the number, starting with `b`.

For example, `review-spec-fidelity-opus-mr4-1` orders a spec-fidelity review of `my-org/my-repo#4` on the `opus` tier,
and `review-spec-fidelity-opus-mr4-2b` repeats the order `review-spec-fidelity-opus-mr4-2`.
`implement-opus-mr3-1` orders the first build of the issue `my-org/my-repo#3` on the `opus` tier.

Each Routine picks its orders by branch name.
A name off its pattern would start no session or the wrong one.
So would a name that disagrees with the order's role, pass, tier or subject.
The order check fails such an order and names the part of the name that is off.

## The order file

```yaml
id: review-spec-fidelity-opus-mr4-1
role: reviewer
pass: spec-fidelity
model_tier: opus
subject: my-org/my-repo#4
checkouts:
  pandoscope/skills: main
tickets:
  - my-org/my-repo#3
```

- `id`: the order's name.
  It must equal the file's name without `.yml`, or `pandoscope order check` fails.
- `role`: the worker role, such as `reviewer` or `implementer`.
  An order sets a worker role only; only the principal sets the role orchestrator.
- `model_tier`: the model tier that runs the session.
  Every order sets `model_tier`, because each Routine serves one tier.
  An order without one fails the order check.
  In a review order it is the same value as in the name.
- `pass`: the kind of review, the same value as in the name.
  A `reviewer` order sets it, and an order of any other role sets none.
- `subject`: the issue or pull request that the order works on, written `org/repo#n`.
  The forge numbers the issues and pull requests of a repository in one sequence, so `org/repo#n` names exactly one of them.
  A review order names the reviewed pull request.
  An implementer order names an issue for the first build, before any pull request exists.
  For a fix round, it names the implementer's own pull request.
  The composer reads the subject from its repository's clone, so that repository needs no entry under `checkouts`.
- `base`, optional: the branch that the reviewed pull request targets, or that a first build starts from.
  Set it when that branch is not the repository's default branch, as in a stack of pull requests.
- `checkouts`, optional: one line `org/repo: ref` for each repository that the session must read at a ref other than its default branch.
  The lines are not nested.
  A session-start hook switches the clone of each listed repository to its ref before the composer runs.
  The hook never switches the clone of this repository, which stays at the head of the order's pull request.
- `tickets`: for a review, the tickets that the reviewed pull request implements.
  A review reads them as its specification.
  For an implementer, the tickets that the work implements besides an issue named as its subject.
  The driver does not let a session end before it has read every listed ticket.
- `result`: not used.
  The result is a file on the order's branch, described below.

### A prose review order

A prose review order sets `pass: prose`, and its branch is `order/review-prose-<model_tier>-<code><n>-<step>`.
It also lists `pandoscope/skills` under `checkouts`, at a ref that holds the folder `original/writing-prose/rules/`.
The reviewer reads the writing rules from that folder.
The branch `main` holds it.

## The result

The result is a JSON file that a schema defines.
The driver and the worker share it:

1. At session start, before the worker gets control, the driver creates the file.
   A script records the session id and the session's setup: the role, and the ref of each checkout,
   with no value that must not leave the session.
   The driver marks the file incomplete and pushes it at once.
2. The worker writes the result's content into the file while it works.
3. The driver validates the file on every write of the worker and before every push, and the worker fixes a file that fails.
   At the end of every session, whatever the outcome, the driver pushes the result as its last act.
   The driver writes into the file itself only when the worker failed to write it properly: an error message or a state.

A worker commits only to the order's branch, and the driver pushes to origin.
So in this repository a worker changes only the result file on the order's branch.
An implementer's code commits are not covered by that limit.

What the worker writes depends on its role:

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
  the commit the worker read is still the head of the pull request it worked on,
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
Everything else comes from the order: the role, the pass, the subject and the checkouts.
The environment cannot come from the order, so it stays with the Routine:
the model and the repositories attached.

Each Routine has a filter on the branch of the order's pull request.
The Routine for the `opus` tier has the filter `order/[0-9a-z-]+-opus-.*`,
so it starts a session for every example order above.

The principal has these duties:

- Before a Routine starts its first session, the principal checks that the Routine runs a model of its tier.
- The principal attaches to each Routine every repository that an order may need,
  because a session cannot clone a private repository it was not started with.
  A session in a cloud environment removes the clones its task does not need,
  and keeps the stores and the checkouts that the hooks read.

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
  or the worker failed to fix a result that the check failed;
  the principal or the orchestrator that placed the chain is told at once.
  An order is also stuck when its worker has written nothing to the thread ledger for an hour and no final result is on the order's branch;
  the principal is told at once.
  Stuck orders are reported one at a time.
  A session that dies without pushing its final result is a defect of the chain.
- **Abandoned:** Pando or the principal has closed an order that was placed by mistake,
  with the reason written in the order's pull request.

No order is closed or deleted silently.

## Not built yet

This README states the design that the principal ruled on 2026-11-17 and 2026-11-18.
These parts do not exist yet:

- the result schema, the result check, and the order schema's changes for worker roles:
  pandoscope/pandoscope#54 and agentic-engineering-template#330;
- the driver's writes of the result: pandoscope/skills#278.

Until they exist, a review driver pushes its findings to a branch of the reviewed repository,
and the findings are answered in a **disposition file**.
Until the result check exists, the session that placed the order or the principal closes it once its findings are answered.

## Dispositions

A review that runs outside a chain has no result on an order's branch.
Its findings are answered in this repository, in a **disposition file** `dispositions/<name>.yml`,
and not in comments on the forge.
That keeps the record in git, so it does not depend on which forge hosts the repositories.
[dispositions/README.md](dispositions/README.md) describes the format.

## One way only

An order may list a Pandoscope repository under `checkouts`, such as `pandoscope/skills`, which holds the rules that a review reads.
A session only reads such a clone.

Nothing goes the other way.
No session writes one of this org's orders, results, findings or **disposition files**, or the name of one of this org's repositories, into a Pandoscope repository.
Pandoscope's repositories serve every org that Pandoscope sets up, so they carry no single org's work.
This is a rule that every session follows; nothing in this repository can enforce it.
