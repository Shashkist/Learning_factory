#!/usr/bin/env bash
# docs_only.sh BASE HEAD: exit 0 when every path changed in BASE..HEAD is docs-only
# (see docs-only-paths.txt), also when nothing changed; exit 1 when any path is code.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docs_lib.sh
. "$here/docs_lib.sh"
[ "$#" -eq 2 ] || { echo "usage: docs_only.sh BASE HEAD" >&2; exit 2; }
if git diff --name-only "$1" "$2" | only_docs; then exit 0; fi
exit 1
