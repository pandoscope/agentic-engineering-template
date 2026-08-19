# Session Memory

> Copier-vendored from the agentic-engineering-template session
> subtemplate — do NOT edit in the store repo; change it in the
> template and pull via `copier update`.

What happened, per session: append-only **thread events** — what each
line of work is, what state it is in, and which conversation moved it
— plus the conversation exports those events anchor to. The third
memory store, beside a decision store (rulings) and an evidence store
(detections).

The data is this repo's; the recorder that writes it ships with the
`thread-ledger` skill, so N stores cannot drift from one schema.

## What lives here, and what does not

| Here | Not here |
| --- | --- |
| Thread events: opened, progress, blocked, terminal | Status as a field anyone edits. State is the fold of the log. |
| The conversation each event came from, by URL | The tooling that writes them — that ships with the skill |
| Exported conversation text, for cross-session search | Tool results, which carry command output and environment values |

The store is **memory, not a backlog**. Every open thread names a
forge ticket or is tagged `conversation-only`, and the work is tracked
there. Two backlogs would mean one stale backlog, and the recorder
rejects an entry that claims neither.

## Layout

- `ledger/<session-id>.jsonl` — one file per conversation, named for
  the conversation's URL. Append-only. Events interleave by their own
  stamp across files; within a file, line order is the tiebreak and is
  load-bearing.
- `ledger/<writer>.jsonl` — events written by something that is not a
  conversation. They carry `by:` and no session URL, because there is
  no conversation to link to.
- `transcripts/<session-id>/` — exported conversation text.
- `LEDGER.md` — the folded state as markdown. Rendered, not written:
  `.github/workflows/render-ledger.yml` regenerates it on manual
  dispatch only, so it may lag the log; the fresh view is the local
  heartbeat render (`LEDGER_RENDER_PATH`). Edits to it are overwritten
  on the next dispatch.
- `repo-codes.json` — short codes for ticket prefixes in the rendered
  view. Seeded once and store-owned, because the template ships no
  org's names; an unmapped repo renders under its own name, so a
  missing entry is visible rather than silently wrong.

## How it is written

Through the recorder, never by hand: it stamps the fields code owns
and rejects a transition the state machine forbids. Pushed straight to
`main` — no PRs, no review gate. The contract is the schema and the
recorder, not a human reading every append.

A wrong event is corrected by a later one, never by editing the log.
That is what makes the store a record of what was believed when,
rather than only of what turned out to be true.

## Closing the loop

A workflow closes a thread when the thread's own ticket closes: work
is done when its ticket says so, and noticing it should not depend on
an agent session happening to be running. Stamped repos report their
ticket closes here by `repository_dispatch` — the template ships that
sender, and it resolves this store from the `SESSION_MEMORY_REPO`
variable — and the receiver, `.github/workflows/close-loop.yml`,
answers each one by checking every live
thread's ticket, appending `completed`, or `dropped` for a ticket
closed as not planned.

Tickets it cannot read are named in the run summary as unchecked,
never silently skipped: a reconciler that goes quiet about what it did
not check reads exactly like one that found nothing.

It writes terminal events and nothing else. Reopening a thread whose
ticket closed too early stays a human's call — and is the correction
to make when the workflow got one wrong.

## What goes in a transcript export

User and assistant message text only. Tool results are never exported:
they carry command output and environment values. Exports are
secret-scanned, and known-sensitive variables are redacted by name.

Search starts as `rg` over a clone. A vector index earns its place
against a measured miss rate from a hand-read sample, not before.
