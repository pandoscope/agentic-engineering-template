# Waybill — agent rules

- `orders/` holds one file per order.
  Never edit an order another session is on;
  add a result pointer to your own and push it.
- The order's branch is `order/<name>` and its file is `orders/<name>.yml`, the same `<name>`.
  A Routine's filter matches the branch prefix, so the name decides which Routine fires.
- The name ends in the reviewed repository's short code and the pull request's number,
  because each repository numbers its pull requests from 1.
- `checkouts` is flat: two-space-indented `owner/repo: ref` lines.
  The session-start hook parses exactly that shape.
- One way only: an order may pin a Pandoscope repository read-only,
  but no order, finding, disposition or name of this org's repositories
  is written into a Pandoscope repository.
- Never write a token, a secret or a session transcript here.
