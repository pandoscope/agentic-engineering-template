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
#   --files <file>...  pre-commit: the staged files, path and content.
#   --message <file>   commit-msg: the message file git hands the hook.
#   --push             pre-push: the pushed ref's name, an annotated tag's
#                      tagger and message, and every commit about to leave
#                      — message, author and committer name and email,
#                      changed paths, and the added side of its diff. Commits made elsewhere,
#                      or before the variable was loaded, never saw the
#                      other two stages. The range comes from prek's
#                      environment (PRE_COMMIT_LOCAL_BRANCH, PRE_COMMIT_FROM_REF,
#                      PRE_COMMIT_TO_REF); run by hand, the commits HEAD
#                      holds beyond the remote stand in. Fails closed: a git
#                      call that fails is a refused push, never zero commits
#                      judged.
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

# Case-insensitive matching folds non-ASCII letters only in a UTF-8
# locale; a hook shell often runs in C, where a name with an umlaut
# in another casing would pass.
locales="$(locale -a 2>/dev/null || true)"
for candidate in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8; do
    case $'\n'"$locales"$'\n' in
        *$'\n'"$candidate"$'\n'*) export LC_ALL="$candidate"; break ;;
    esac
done

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
position=0

# scan <surface>: stdin text against every entry; a hit names the
# surface and the entry's label. grep reads a here-string, never a
# pipe: `grep -q` leaves at its first match, a writer still feeding a
# large text then dies of SIGPIPE, and under pipefail that hit would
# read as "no match".
scan() {
    local surface="$1" text i
    text="$(cat)"
    for i in "${!values[@]}"; do
        if grep -qiF -e "${values[$i]}" <<<"$text"; then
            echo "$surface carries PUSH_BLOCKLIST ${labels[$i]}" >&2
            hits=$((hits + 1))
        fi
    done
}

# scan_file <file>: its path, then its content. A path that carries a
# value is itself unprintable, so the file is then named by position.
scan_file() {
    local file="$1" shown="$1" before="$hits" i
    position=$((position + 1))
    scan "the path of staged file $position" <<<"$file"
    [ "$hits" -eq "$before" ] || shown="staged file $position"
    [ -f "$file" ] || return 0
    for i in "${!values[@]}"; do
        if grep -qiF -e "${values[$i]}" -- "$file"; then
            echo "$shown carries PUSH_BLOCKLIST ${labels[$i]}" >&2
            hits=$((hits + 1))
        fi
    done
}

# git_text <args>: git's stdout, or a refused push. A failed git call
# inside a process substitution would be ignored and judge nothing.
git_text() {
    local text
    text="$(git "$@")" || {
        echo "push-blocklist: git $1 failed; refusing to judge the push on a partial read" >&2
        exit 1
    }
    printf %s "$text"
}

# push_range: the commits this push publishes, one sha per line.
push_range() {
    local from="${PRE_COMMIT_FROM_REF:-}" to="$1"
    local remote="${PRE_COMMIT_REMOTE_NAME:-origin}"
    # prek hands over the remote's old tip, or for a new branch the
    # parent of the first unpushed commit — the empty tree when there
    # is none, which is not a commit and cannot bound a range.
    if [ -n "$from" ] && [ "$(git cat-file -t "$from" 2>/dev/null || true)" = commit ]; then
        git_text rev-list "$from..$to"
    else
        git_text rev-list "$to" --not --remotes="$remote"
    fi
}

scan_commit() {
    local sha="$1" short identity message diff paths
    short="$(git_text rev-parse --short "$sha")" || exit 1
    identity="$(git_text log -1 --format='%an%n%ae%n%cn%n%ce' "$sha")" || exit 1
    message="$(git_text log -1 --format=%B "$sha")" || exit 1
    diff="$(git_text diff-tree --root -r -p -m --first-parent --no-commit-id --no-color "$sha")" || exit 1
    scan "commit $short author/committer identity" <<<"$identity"
    paths="$(git_text diff-tree --root -r -m --first-parent --no-commit-id --name-only "$sha")" || exit 1
    scan "commit $short message" <<<"$message"
    scan "commit $short changed paths" <<<"$paths"
    scan "commit $short added diff" \
        <<<"$(printf '%s\n' "$diff" | { grep '^+' || true; } | { grep -v '^+++ ' || true; })"
}

# scan_push: the ref's name, a tag's own object, then its commits.
scan_push() {
    local ref="${PRE_COMMIT_LOCAL_BRANCH:-$(git symbolic-ref -q HEAD 2>/dev/null || true)}"
    local to="${PRE_COMMIT_TO_REF:-HEAD}" kind tag shas sha
    [ -z "$ref" ] || scan "the pushed ref name" <<<"$ref"
    # A deleted ref publishes nothing: git hands over the null sha.
    case "$to" in *[!0]*) ;; *) return 0 ;; esac
    kind="$(git_text cat-file -t "$to")" || exit 1
    if [ "$kind" = tag ]; then
        tag="$(git_text cat-file -p "$to")" || exit 1
        scan "tag ${ref#refs/tags/} tagger and message" <<<"$tag"
        to="$(git_text rev-parse "$to^{commit}")" || exit 1
    fi
    shas="$(push_range "$to")" || exit 1
    for sha in $shas; do scan_commit "$sha"; done
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
        scan_push
        ;;
esac

[ "$hits" -eq 0 ] && exit 0
cat >&2 <<MSG
push-blocklist: $hits hit(s); the matched values stay unprinted. Scrub the text
  and rewrite the offending commit — the entry's placeholder names which value
  (AGENTS.md § Leak Prevention).
MSG
exit 1
