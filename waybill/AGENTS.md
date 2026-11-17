# Waybill — agent rules

- One order: `order/<name>` branch, `orders/<name>.yml` file, one pull request, never merged. Name picks Routine. Name rule: [README](README.md#an-order).
- Only Pando or the principal place the first order of a chain. A worker never opens or closes an order: the result check does.
- Worker: change only the result file. Driver writes and pushes it at start (incomplete) and at end. Never touch the order file.
- Discovery outside the task: name an existing ticket in the result, else record it there. Never write to the tracker.
- `checkouts`: flat, two-space `org/repo: ref` lines. Session-start hook parses only that.
- Review outside a chain: answer findings in a disposition file, format in [dispositions/README.md](dispositions/README.md).
- One way only: [README](README.md#one-way-only).
- Never write token, secret, session transcript.
