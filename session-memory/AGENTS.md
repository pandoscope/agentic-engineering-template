# Session-Memory Store — Agent Guidelines

> Copier-vendored from the agentic-engineering-template session
> subtemplate — do NOT edit in the store repo; change it in the
> template and pull via `copier update`.

What happened, per session: thread events, and the conversation
exports they anchor to. The data is this repo's; the recorder that
writes it lives with the `thread-ledger` skill, so N stores cannot
drift from one schema.

## Golden rules

- `ledger/` is append-only. NEVER edit or delete an event — a wrong
  event is corrected by a later one (`reopened` after a mistaken
  `completed`), which is why the log doubles as a record of what was
  believed when.
- **Write through the recorder, never by hand.** It stamps the fields
  code owns and validates the transition; a hand-written line can
  encode a state the machine forbids, and nothing downstream will
  notice.
- This store is **memory, not a backlog**. Every open thread names a
  forge ticket or is tagged `conversation-only`, and work is tracked
  on the board. Two backlogs would mean one stale backlog.
- One log file per conversation, named for the conversation's URL. A
  second file for one conversation folds in beside the first and
  nothing looks wrong — both are valid, the state is merely built from
  the wrong one.
- Events interleave by their own stamp across files, and line order
  within a file is the tiebreak. Both matter: the recorder validates
  each append against that fold, so an ordering bug lets an illegal
  transition through.
- Never write this repo's URL into a public artifact.

## Merge approval

This store has no merge-approval gate, by ruling (template ticket
252): the ledger takes direct pushes from every session by design,
so a human Approve review in front of each push would gate nothing.
The org-wide audit (meta ticket 132) exempts session-memory by name,
not by silence; decision-memory and evidence-memory do carry the
gate, because the release bot merges their pull requests.

## What a transcript export may contain

User and assistant message text only. Tool results are never exported
— they carry command output and environment values. Exports are
secret-scanned, and known-sensitive variables are redacted by name.

## Who else writes here

A workflow closes a thread when the thread's own ticket closes. Its
events carry `by: bot` and land in their own log, because a workflow
is not a conversation and has neither a position in one nor a URL to
link to. A close it got wrong is corrected the ordinary way, by a
human reopening the thread — the bot only ever writes terminal events.
