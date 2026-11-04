# Dispositions

One file per order, `<name>.yml`, the order's own name,
with one entry per finding of that order:

```yaml
order: review-spec-fidelity-opus-mr4
findings: <org/repo>@<sha>:<path of findings.json>
reviewed_head: <org/repo>@<sha>
entries:
  - finding: 0             # index into findings.json
    at: <file>:<line>
    owner: [<org/repo#n>]    # the pull request whose commits wrote the line
    status: open           # or "fixed <org/repo@sha>", or "no-commit: <why>"
    note: "<optional>"
```

- The session that placed the order writes the file once the findings exist,
  sets each entry's `owner` from `git blame` at the reviewed head,
  and pushes the file straight to `main`.
- The session behind each pull request named in `owner` fixes that entry in its own commits and sets its status;
  nobody fixes lines another pull request wrote.
- A finding with several pull requests in `owner` lists them all; each fixes its part.
