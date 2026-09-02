#!/usr/bin/env bash
# The glossary prune, run for real on the template's own root (#210).
#
# Consumers converge by deletion: every stamp re-delivers the shared
# term set and `disambiguate prune` drops what nothing links (#67,
# #203). This root is different. Its glossary is render output that
# tests/test_self_application.py pins byte-for-byte, and the Glossary
# index in README.md is what keeps every term reachable. So the same
# prune runs here with the same pin, but a removal is not convergence,
# it is a defect: a README link dropped, or a term nothing reaches.
# The hook fails loudly, names the terms, and puts tracked ones back
# so a commit hook never leaves the tree mutated.
#
# The pin is copier.yml's default for agentic_disambiguate_version:
# the root has no answers file (.copier-answers.agentic.yml is a
# deliberate divergence), and reading the default keeps one source.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

pin="$(awk '
    $1 == "agentic_disambiguate_version:" { in_q = 1; next }
    in_q && $1 == "default:" { gsub(/"/, "", $2); print $2; exit }
    in_q && /^[^ #]/ { in_q = 0 }
' copier.yml)"
if [ -z "$pin" ]; then
    echo "prune_glossary: copier.yml has no agentic_disambiguate_version default" >&2
    exit 1
fi

glossary=docs/glossary
list_terms() { find "$glossary" -maxdepth 1 -name '*.md' | sort; }

before="$(list_terms)"
uvx "disambiguate==$pin" prune
removed="$(comm -23 <(printf '%s\n' "$before") <(list_terms))"

[ -n "$removed" ] || exit 0

{
    echo "GLOSSARY PRUNE REMOVED TERMS FROM THE TEMPLATE ROOT"
    while IFS= read -r file; do
        if git checkout --quiet -- "$file" 2>/dev/null; then
            echo "  $file  (restored)"
        else
            echo "  $file  (untracked: not restorable, re-create it)"
        fi
    done <<< "$removed"
    echo "Nothing reachable from README.md links them. On this root the"
    echo "glossary is render output pinned by tests/test_self_application.py,"
    echo "so a removal is a defect, not convergence: link each term from the"
    echo "Glossary index in README.md (or from the doc that should reach it),"
    echo "or drop it from template/docs/glossary/ and restamp."
} >&2
exit 1
