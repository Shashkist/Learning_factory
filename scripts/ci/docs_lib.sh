# Sourced by docs_only.sh and latest_code_commit.sh. Not run on its own.
# In bash patterns `*` also matches `/`, so `docs/**` and `**.md` behave like the GitHub globs.
_docs_list="$(dirname "${BASH_SOURCE[0]}")/docs-only-paths.txt"

# is_docs_path PATH: exit 0 when PATH matches a glob of the list.
is_docs_path() {
  local path="$1" line
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ""|"#"*) continue ;; esac
    # shellcheck disable=SC2053
    if [[ "$path" == $line ]]; then return 0; fi
  done < "$_docs_list"
  return 1
}

# only_docs: read paths on stdin; exit 0 when all are docs-only (also when there are none).
only_docs() {
  local p
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    if ! is_docs_path "$p"; then return 1; fi
  done
  return 0
}
