# Claude Code Specific Project Instructions

**First:** Read `AGENTS.md`. Follow all instructions there.

## Session Role

A session works only with a role.
The session's composed instructions open with the line `# Role: <role>`.
A session whose context holds no such line does no work:
its first reply tells the principal that the session has no role,
which happens when the session's setup did not run or failed,
and the session stops there.
Composed instructions that open with `# No role` are the same case:
reply with the text they give.

## Pull Requests

Share PR URL in response to user.
