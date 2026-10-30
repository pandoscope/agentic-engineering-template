# Waybill — agent rules

- One order: `order/<name>` branch, `orders/<name>.yml` file, one pull request. Name picks Routine. Name rule: [README](README.md#an-order).
- Never edit another session's order. Own order: add result pointer, push.
- `checkouts`: flat, two-space `org/repo: ref` lines. Session-start hook parses only that.
- Findings: answer in `dispositions/`, format in [its README](dispositions/README.md).
- One way only: [README](README.md#one-way-only).
- Never write token, secret, session transcript.
