"""CI path rules (#271): which commits are docs-only, which PRs need the heavy jobs.

  ci_paths.py latest-code <rev>        sha of the newest first-parent commit that changes code
  ci_paths.py deploys <tag> <head>     "true" or "false": may a green CI for <tag> deploy (main is <head>)
  ci_paths.py heavy <base> <head>      "true" or "false": does the PR touch a heavy path
"""

import subprocess
import sys

# Directories whose changes alone never need CI or a Deploy (keep in step with ci.yml paths-ignore).
DOCS_ONLY = ("docs", "courses", ".claude")
# Directory prefixes whose changes need the docker and image jobs.
HEAVY = ("labs/images/", "labs/manager/", "labs/deploy/", "labs/content/", ".github/workflows/")


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        check=True,
        capture_output=True,
    )
    return out.stdout.decode("utf-8")


def latest_code(rev: str) -> str:
    # Pathspec exclusion: git decides what is docs-only, no path text is parsed here.
    spec = [".", *(f":(exclude){d}" for d in DOCS_ONLY)]
    sha = git("rev-list", "-1", "--first-parent", rev, "--", *spec).strip()
    if not sha:
        raise SystemExit(f"no code commit found in the first-parent history of {rev}")
    return sha


def deploys(tag: str, head: str) -> bool:
    # A CI run for <tag> may deploy when no code landed on main after it. Docs-only commits on top
    # of <tag> do not count (they run no CI). Comparing the newest code commit of both sides, not
    # <tag> itself, also covers one push that carried a code commit with a docs commit on top: CI
    # then runs only for the docs head, and its image holds the same code.
    return latest_code(tag) == latest_code(head)


def is_heavy(base: str, head: str) -> bool:
    names = git("diff", "--name-only", "-z", f"{base}...{head}").split("\0")
    return any(n.startswith(HEAVY) for n in names if n)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "latest-code":
        print(latest_code(argv[1]))
        return 0
    if len(argv) == 3 and argv[0] == "deploys":
        print("true" if deploys(argv[1], argv[2]) else "false")
        return 0
    if len(argv) == 3 and argv[0] == "heavy":
        print("true" if is_heavy(argv[1], argv[2]) else "false")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
