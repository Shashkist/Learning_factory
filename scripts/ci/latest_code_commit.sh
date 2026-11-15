#!/usr/bin/env bash
# latest_code_commit.sh [REF]: print the sha of the newest commit reachable from REF
# (default HEAD) that touched a path outside docs-only-paths.txt.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docs_lib.sh
. "$here/docs_lib.sh"
ref="${1:-HEAD}"
for c in $(git rev-list "$ref"); do
  if ! git diff-tree -r --no-commit-id --name-only --root "$c" | only_docs; then
    echo "$c"
    exit 0
  fi
done
echo "no code commit found from $ref" >&2
exit 1
