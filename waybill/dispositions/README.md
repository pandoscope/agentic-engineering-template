# Disposition files

A review that runs outside a chain of orders answers its findings in a disposition file here.
Inside a chain, the results on the order's branch carry the findings and their answers, and no disposition file is written.

This folder holds one disposition file per such review.
The file is named `<name>.yml`: the name of the review's order, or a name for the review when no order started it.
It has one entry for each finding:

```yaml
order: review-spec-fidelity-opus-mr4
findings: <org/repo>@<sha>:<path of the findings>
reviewed_head: <org/repo>@<sha>
entries:
  - finding: 0             # index into the findings
    at: <file>:<line>
    owner: [<org/repo#n>]    # the pull request whose commits wrote the line
    status: open           # or "fixed <org/repo@sha>", or "no-commit: <why>"
    note: "<optional>"
```

- `findings`: the repository, the commit and the path of the file that holds the review's findings.
  A review driver that runs outside a chain commits them as `reviews/<pass>-<model_tier>/findings.json`
  on a branch `claude/review-<pass>-<model_tier>-pr<n>` of the reviewed repository.
- `reviewed_head`: the reviewed repository and the commit that the review read.
- `finding`: the position of the finding in the list of findings, counted from 0.

## Who writes what

- The session that started the review writes the disposition file once the findings exist.
  It sets the `owner` of each entry from `git blame` of the reported line at the reviewed head,
  and pushes the file straight to `main` of this repository.
- The owner of an entry is a pull request.
  The session that works on that pull request fixes the reported line in a commit of that pull request.
  It then sets the entry's `status` to `fixed`, with the fixing commit, and pushes that change straight to `main` of this repository.
  No session fixes a line that another pull request wrote.
- A session sets `no-commit` when it answers a finding without a commit:
  the finding is wrong, the line is already fixed, or the line is left as it is on purpose.
  The reason follows the colon.
- An entry lists several pull requests in `owner` when the finding spans lines that several of them wrote.
  The session of each owner fixes the lines of its own pull request and records its commit in `note`.
  The `status` stays `open` until every owner has fixed its lines;
  it then becomes `fixed` and lists every fixing commit.
