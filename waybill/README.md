# Waybill

Sessions use this repository to order reviews and to answer the findings of those reviews.
An order describes one task that a session carries out on one pull request.
Today every order asks for a review.

A session that wants a pull request reviewed is called the placing session here.
The placing session writes one order file under `orders/`,
pushes the file on a new branch of this repository and opens a pull request for that branch.
Opening that pull request makes a Routine start a review session,
and the review session reads the order file from its checkout of the branch.

So every order involves two pull requests:

- the order's pull request, in this repository, which carries the order file;
- the reviewed pull request, in another repository, which the order names.

## Terms

- **Principal:** the person who owns this org's agents and approves their work.
- **Forge:** the service that hosts the repositories and their pull requests, such as GitHub.
- **Pandoscope:** the product that stamped this repository from its template.
  Pandoscope's own repositories are in the forge org `pandoscope`.
- **Harness:** the environment that gives a model tools, files and a session, such as Claude Code.
- **Routine:** a configuration that the principal saves on the harness: a prompt, a model and the repositories to clone.
  When the Routine's event occurs, the harness starts a session from that configuration.
  Here the event is a pull request opened in this repository.
- **Ticket:** an issue on the forge, written `org/repo#n`.
- **Pass:** one kind of review.
  The pass `spec-fidelity` checks that the reviewed pull request does what its tickets ask;
  the pass `prose` checks its text against the writing rules.
- **Model tier:** the family of models that runs a session, such as `opus`.
- **Finding:** one fault that a review reports, with its file and line.
- **Disposition:** the answer to one finding: fixed, or not fixed and why.
- **Review driver:** the program that holds a review session to the rules of a review.
  The harness runs the review driver through its hooks.
- **Composer:** the Pandoscope command that writes a session's task from the order.

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

The pattern is a convention: `pandoscope order check` accepts any name of lower-case letters, digits and hyphens.
The Routines depend on the convention, because each Routine picks its orders by branch name.

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
- `role`: what the session does.
  A review order sets `reviewer`.
  The schema also allows `orchestrator`, `implementer`, `general` and `probe`; this document covers only review orders.
- `pass` and `model_tier`: the same two values as in the name.
  A `reviewer` order must set both, and an order of any other role must set neither.
  The review driver reads them from the file, while the Routine is picked by the branch name,
  so the file and the name must agree.
- `pull_request`: the reviewed pull request, written `org/repo#n`.
  The composer fetches its head, so the reviewed repository needs no entry under `checkouts`.
- `base`, optional: the branch that the reviewed pull request targets.
  Set it when that branch is not the repository's default branch, as in a stack of pull requests.
- `checkouts`, optional: one line `org/repo: ref` for each repository that the session must read at a ref other than its default branch.
  The lines are not nested.
  A session-start hook switches the clone of each listed repository to its ref before the composer runs.
  A clone that is not listed stays on its default branch.
  The hook never switches the clone of this repository, which stays at the head of the order's pull request.
- `tickets`: the tickets that the reviewed pull request implements,
  which are the tickets that its description references.
  A review reads them as the specification.
  The review driver does not let a review session end before the session has read every listed ticket.
- `result`, optional: a pointer to what the session produced.
  No review sets it, because a review's result is its findings file, which the disposition file points to.

### A prose review order

A prose review order sets `pass: prose`, and its branch is `order/review-prose-<model_tier>-<code><n>`.
It also lists `pandoscope/skills` under `checkouts`, at a ref that holds the folder `original/writing-prose/rules/`.
The reviewer reads the writing rules from that folder.
The branch `main` holds it.

## The Routines

The principal saves one Routine for each model tier.
One Routine serves every pass of its tier, because the Routine's prompt carries no task: the order does.

Each Routine has a filter on the branch of the order's pull request.
The Routine for the `opus` tier has the filter `order/review-[0-9a-z-]+-opus-.*`,
so it starts a session for the example order above.
An order for a tier that has no Routine starts no session.
The placing session asks the principal which tiers have one.

Two duties are the principal's:

- Before a Routine starts its first session, the principal checks that the Routine runs a model of its tier.
- The principal attaches to each Routine every repository that an order may list under `pull_request` or `checkouts`.
  A repository that is not attached has no clone in the session.

## What the harness hands the session

The harness checks out the head of the order's pull request.
It describes that pull request in the environment variables `CCR_TRIGGER_REPO`, `CCR_TRIGGER_PR_NUMBER`,
`CCR_TRIGGER_HEAD_REF`, `CCR_TRIGGER_HEAD_SHA` and `CCR_TRIGGER_BASE_REF`.
The session's own order is the file `orders/<name>.yml` for the branch `order/<name>` in `CCR_TRIGGER_HEAD_REF`.

## The lifecycle of an order

An order's pull request is a message to the Routine, and it is never merged.
The branch `main` therefore holds no order files; the record of a review is its disposition file.

- **Open:** the order's pull request is open.
  A review session is running for it or has ended.
- **Done:** the placing session has written the order's disposition file,
  and has then closed the order's pull request and deleted the order's branch.
- **Abandoned:** the placing session has closed the order's pull request and deleted the order's branch without writing a disposition file,
  for example because the order was wrong.

## Dispositions

A review writes its findings to one file, which the review driver pushes to a new branch of the reviewed repository.
The findings are answered in this repository and not in comments on the forge.
That keeps the record in git, so it does not depend on which forge hosts the repositories.
Each order has one disposition file, `dispositions/<name>.yml`;
[dispositions/README.md](dispositions/README.md) describes its format.

## One way only

An order may list a Pandoscope repository under `checkouts`, such as `pandoscope/skills`, which holds the rules that a review reads.
A session only reads such a clone.

Nothing goes the other way.
No session writes one of this org's orders, findings or dispositions, or the name of one of this org's repositories, into a Pandoscope repository.
Pandoscope's repositories serve every org that Pandoscope sets up, so they carry no single org's work.
This is a rule that every session follows; nothing in this repository can enforce it.
