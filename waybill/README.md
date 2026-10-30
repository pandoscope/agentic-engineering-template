# Waybill

The order a session carries.
One file per order under `orders/`, pushed on its own branch and opened as a pull request;
opening the pull request fires the Routine whose head-branch filter matches,
and the fired session finds the order in its checkout.

## An order

Branch `order/<name>`, file `orders/<name>.yml`,
where `<name>` says what the order is:
`review-spec-fidelity-opus-<code><n>` for a review.
The name ends in the reviewed repository's short code and the pull request's number,
because each repository numbers its pull requests from 1:
`review-spec-fidelity-opus-mr4` reviews `my-org/my-repo#4`.
A repository's short code is the one its branch names use (`claude/<code><ticket>-…`).

Each model tier has one Routine, which serves every pass,
because its prompt is the same for any order.
The Routine `review:opus` fires for head branches that match `order/review-[0-9a-z-]+-opus-.*`,
so it fires for this order.
The principal checks each Routine's model before the Routine fires for the first time.

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

- `pull_request`: `owner/repo#n`, the pull request under review.
  The composer reads it and fills the review task.
- `checkouts`: one ref per repository, flat.
  The session-start hook switches each named clone to that ref before the composer runs;
  the waybill clone itself stays at the pull request head.
- `tickets`: what the order advances.

### A prose review order

A prose review order sets `pass: prose`, on a branch `order/review-prose-<model_tier>-<code><n>`.
The Routine of its model tier fires it, as for any other pass.

- The order pins `pandoscope/skills` under `checkouts` to a ref that holds `original/writing-prose/rules/`.
  The reviewer reads the rules from that checkout.
- The order lists under `tickets` every ticket that the pull request carries.
  The driver refuses to end the session until the session has read each one.

## What a fire hands the session

The harness sets `CCR_TRIGGER_REPO`, `CCR_TRIGGER_PR_NUMBER`, `CCR_TRIGGER_HEAD_REF`,
`CCR_TRIGGER_HEAD_SHA` and `CCR_TRIGGER_BASE_REF` in the environment
and checks the pull request head out.
Nothing is read from the prompt but the Routine's marker line.
Every repository whose pull requests are reviewed, and every repository an order pins,
is attached to the Routine, so the clones the order names exist.

## Lifecycle

Open: the pull request is open and a session is on it.
Done: the session pushes its result pointer to the order branch
and the pull request is merged into `main`, which is the record.
Abandoned: the pull request is closed unmerged.

This repository takes direct pushes by design and carries no pull request gate and no merge bot.

## Dispositions

A review's findings are answered in this repository, not in forge comments,
so the record stays in git wherever the repositories live.
`dispositions/<order id>.yml` holds one entry per finding of that order:

```yaml
order: review-spec-fidelity-opus-mr4
findings: <owner/repo>@<sha>:<path of findings.json>
reviewed_head: <owner/repo>@<sha>
entries:
  - finding: 0             # index into findings.json
    at: <file>:<line>
    owner: [<owner/repo#n>]  # the pull request whose commits wrote the line
    status: open           # or "fixed <owner/repo@sha>", or "no-commit: <why>"
    note: "<optional>"
```

- The session that placed the order writes the file once the findings exist,
  and sets each owner from `git blame` at the reviewed head.
- Each owner fixes its entries in its own commits and sets their status;
  nobody fixes another owner's lines.
- A finding with several owners lists them all; each fixes its part.
- The file is pushed straight to `main`, like the orders' results.

## One way only

Orders here may pin Pandoscope repositories, read-only, such as the skills a review reads its rules from.
Nothing goes the other way:
no order, finding, disposition or name of this org's repositories is written into a Pandoscope repository,
because Pandoscope serves every org it sets up, and its repositories never carry one org's work.
