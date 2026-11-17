# Dispositions

This folder holds one file per order.
The file is named `<name>.yml`, where `<name>` is the order's name,
and it has one entry for each finding of that order's review:

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

## Where the findings are

The review driver commits the findings file as `reviews/<pass>-<model_tier>/findings.json`
on a new branch `claude/review-<pass>-<model_tier>-pr<n>` of the reviewed repository, and pushes that branch.
`<n>` is the number of the reviewed pull request.
The review is finished when that branch exists on the forge.

The findings file is a JSON object.
Its field `head` is the commit that the review read, and its field `findings` is the list of findings.

- `findings`: the reviewed repository, the commit that added the findings file, and the file's path.
- `reviewed_head`: the reviewed repository and the commit from the findings file's `head`.
- `finding`: the position of the finding in the list, counted from 0.

## Who writes what

- The placing session, which is the session that placed the order, writes the file once the review is finished.
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
