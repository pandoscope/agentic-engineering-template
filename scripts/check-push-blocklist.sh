#!/usr/bin/env bash
# Identity class of the leak-prevention stack (#189): no PUSH_BLOCKLIST
# value in what a commit or a push publishes.
#
# PUSH_BLOCKLIST is `|`-separated values in env, never committed; an
# entry may name its placeholder (`value=pb:name`). The values ARE the
# identifying material this hook keeps off public surfaces, so nothing
# this script prints is a value: a hit names the surface and the entry's
# placeholder (or its field position), never the match.
#
# Three prek stages, one mode each:
#   --files <file>...  pre-commit: the staged files.
#   --message <file>   commit-msg: the message file git hands the hook.
#   --push             pre-push: every commit about to leave — message,
#                      author and committer name and email, and the added
#                      side of its diff. Commits made elsewhere, or before
#                      the variable was loaded, never saw the other two
#                      stages. The range comes from prek's environment
#                      (PRE_COMMIT_FROM_REF..PRE_COMMIT_TO_REF); run by
#                      hand, the commits HEAD holds beyond the remote stand in.
#
# An unset or empty variable FAILS (#313): a shell without the list used
# to run green with no identity check at all. Off is explicit —
# PUSH_BLOCKLIST_OFF=1 — and the hook says so when it runs with it.
set -euo pipefail

mode="${1:-}"
case "$mode" in
    --files|--message) shift; action=commit ;;
    --push) shift; action=push ;;
    *) echo "usage: $0 --files <file>... | --message <file> | --push" >&2; exit 2 ;;
esac

if [ "${PUSH_BLOCKLIST_OFF:-}" = 1 ]; then
    echo "push-blocklist: identity check off for this run (PUSH_BLOCKLIST_OFF=1); the server-side gates must cover what it publishes"
    exit 0
fi

# Normalize first: an editor-saved trailing newline or a stray pipe
# would otherwise become an empty entry. Blank entries are dropped;
# whitespace around a value is not part of it.
list="$(printf %s "${PUSH_BLOCKLIST:-}" | tr -d '\r\n')"
values=()
labels=()
n=0
IFS='|' read -r -a fields <<<"$list"
for entry in "${fields[@]+"${fields[@]}"}"; do
    n=$((n + 1))
    value="${entry%%=*}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    [ -n "$value" ] || continue
    label="${entry#*=}"
    [ "$label" != "$entry" ] || label="entry $n"
    values+=("$value")
    labels+=("$label")
done

if [ "${#values[@]}" -eq 0 ]; then
    cat >&2 <<MSG
push-blocklist: PUSH_BLOCKLIST is unset or empty, so the identity leak check cannot run.
  Load the blocklist into this shell first: source the principal's blocklist
  file, which lives outside every repository and exports PUSH_BLOCKLIST
  (AGENTS.md § Leak Prevention). Only where a server-side gate already covers
  what this $action publishes, run without the identity check and say so:
    PUSH_BLOCKLIST_OFF=1
MSG
    exit 1
fi

hits=0

# scan <surface>: stdin text against every entry; a hit names the
# surface and the entry's label.
scan() {
    local surface="$1" text i
    text="$(cat)"
    for i in "${!values[@]}"; do
        if printf %s "$text" | grep -qiF -e "${values[$i]}"; then
            echo "$surface carries PUSH_BLOCKLIST ${labels[$i]}" >&2
            hits=$((hits + 1))
        fi
    done
}

scan_file() {
    local file="$1" i
    [ -f "$file" ] || return 0
    for i in "${!values[@]}"; do
        if grep -qiIF -e "${values[$i]}" -- "$file"; then
            echo "$file carries PUSH_BLOCKLIST ${labels[$i]}" >&2
            hits=$((hits + 1))
        fi
    done
}

# push_range: the commits this push publishes, one sha per line.
push_range() {
    local from="${PRE_COMMIT_FROM_REF:-}" to="${PRE_COMMIT_TO_REF:-HEAD}"
    local remote="${PRE_COMMIT_REMOTE_NAME:-origin}"
    # prek hands over the remote's old tip, or for a new branch the
    # parent of the first unpushed commit — the empty tree when there
    # is none, which is not a commit and cannot bound a range.
    if [ -n "$from" ] && [ "$(git cat-file -t "$from" 2>/dev/null || true)" = commit ]; then
        git rev-list "$from..$to"
    else
        git rev-list "$to" --not --remotes="$remote"
    fi
}

scan_commit() {
    local sha="$1" short
    short="$(git rev-parse --short "$sha")"
    # Process substitution, not a pipeline: scan counts hits in this shell.
    scan "commit $short author/committer identity" \
        < <(git log -1 --format='%an%n%ae%n%cn%n%ce' "$sha")
    scan "commit $short message" < <(git log -1 --format=%B "$sha")
    scan "commit $short added diff" \
        < <(git diff-tree --root -r -p -m --first-parent --no-commit-id --no-color "$sha" \
            | { grep '^+' || true; } | { grep -v '^+++ ' || true; })
}

case "$mode" in
    --files)
        for file in "$@"; do scan_file "$file"; done
        ;;
    --message)
        [ "$#" -eq 1 ] || { echo "usage: $0 --message <file>" >&2; exit 2; }
        scan "the commit message" <"$1"
        ;;
    --push)
        while read -r sha; do
            [ -n "$sha" ] && scan_commit "$sha"
        done < <(push_range)
        ;;
esac

[ "$hits" -eq 0 ] && exit 0
cat >&2 <<MSG
push-blocklist: $hits hit(s); the matched values stay unprinted. Scrub the text
  and rewrite the offending commit — the entry's placeholder names which value
  (AGENTS.md § Leak Prevention).
MSG
exit 1
