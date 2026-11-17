# Waybill — agent rules

- One order: `order/<name>` branch, `orders/<name>.yml` file, one pull request, never merged. Name picks Routine. Name rule: [README](README.md#an-order).
- Only Pando or the principal place the first order of a chain. A worker never opens or closes an order: the result check does, but the principal closes a chain's last order. Until the check exists: the session that placed the order, or the principal, closes it once its findings are answered. Order placed by mistake: Pando or the principal closes it, reason in its pull request.
- Worker: commit only to the order's branch; driver pushes to origin. In this repository the result file is the only file to change, never the order file. Implementer's code commits: not covered. Driver creates the result at start (incomplete) and validates and pushes it; worker writes its content and fixes a file that fails validation.
- Discovery outside the task: name an existing ticket in the result, else record it there. Never write to the tracker.
- `checkouts`: flat, two-space `org/repo: ref` lines. Session-start hook parses only that.
- Review outside a chain: answer findings in a disposition file, format in [dispositions/README.md](dispositions/README.md).
- One way only: [README](README.md#one-way-only).
- Never write token, secret, session transcript.
