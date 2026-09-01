export default {
  extends: ["@commitlint/config-conventional"],
  // commitlint's defaultIgnores silently exempts fixup!/squash!
  // headers (measured: a fixup! commit passed the PR gate unparsed),
  // which hollows out "the type enum IS the allow-list". Off, with
  // only two explicit exemptions: PR branches legitimately merge main
  // in, and git-generated revert headers are not conventional.
  defaultIgnores: false,
  ignores: [
    (message) => message.startsWith("Merge "),
    (message) => message.startsWith('Revert "'),
  ],
  rules: {
    "header-max-length": [0, "always", Infinity],
    "body-max-line-length": [0, "always", Infinity],
    "footer-max-line-length": [0, "always", Infinity],
  },
};
