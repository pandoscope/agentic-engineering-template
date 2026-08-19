# One org, one organism

The glossary defined `org` as "a project as organism", but every memory
store already used "org" for the forge organization: session-memory's
README calls `SESSION_MEMORY_REPO` an "org variable", and `close-loop.yml`
reads "the org's private repos". Two live meanings for one word.

Rather than rename either, the meanings are collapsed deliberately: the
forge organization **is** the boundary that holds the organism. That only
holds as a definition if the mapping is one-to-one, so the cardinality is
part of the term:

- **One org, one project.** A second project means a second org.
- **An org is required for the full organism.** A stamped repo in a
  personal account is supported as a stamped repo — template and skills
  work — but the organism features (memory stores, close-loop, self
  improvement) require an organization.

## Considered Options

- **Allow several organisms per one org** — rejected: every piece of
  org-level wiring would gain a project namespace, so `SESSION_MEMORY_REPO`
  becomes per-project, boards and app installs need scoping rules, and "the
  org's repos" stops meaning "the organism's repos" — the close-loop sweep
  would need a filter it does not have. It also re-opens the ambiguity the
  collapse was meant to close, with a namespacing mechanism competing
  against the org boundary. Revisit if org creation proves a real adoption
  barrier for companies with one established organization.
- **Personal account as a degraded org** — rejected for now: `Org` would
  mean "org or account", and every org-level mechanism would need an
  account-shaped fallback, including the ones that cannot have one (org
  variables, org-private repo sweeps). **Deferred, not refused** — if the
  need arises, supporting a degraded single-project mode is open; the
  present goal is to get the mechanism running first.

## Consequences

- Org-level state needs no namespacing: an org variable, an app install, a
  board, or a memory repo belongs to the one organism by construction.
- Prose may say "org" without disambiguating; both readings are correct.
- The degraded personal-account mode is deferred rather than closed. What
  would reopen it: a concrete personal-account user, or adoption blocked on
  organization creation. Reopening means giving every org-level mechanism a
  declared account-shaped path or an explicit refusal — the fallbacks that
  cannot exist must refuse loudly rather than silently do nothing.
