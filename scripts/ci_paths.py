"""CI path rules (#271): which commits are docs-only, which PRs need the heavy jobs.

  ci_paths.py deploys <tag> <head>     "true" or "false": may a green CI for <tag> deploy (main is <head>)
  ci_paths.py heavy <base> <head>      "true" or "false": does the PR touch a heavy path
"""

import subprocess
import sys

# Directories whose changes alone never need CI or a Deploy (keep in step with ci.yml paths-ignore).
DOCS_ONLY = ("docs", "courses", ".claude")
# Directory prefixes whose changes need the docker and image jobs.
HEAVY = ("labs/images/", "labs/manager/", "labs/deploy/", "labs/content/", ".github/workflows/")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        check=check,
        capture_output=True,
    )


def code_spec() -> list[str]:
    # Pathspec exclusion: git decides what is docs-only, no path text is parsed here.
    return [".", *(f":(exclude){d}" for d in DOCS_ONLY)]


def deploys(tag: str, head: str) -> bool:
    # A green CI for <tag> may deploy when <tag> is on main and main's code is the same tree as
    # <tag>'s: only docs changed since. This is the comparison GitHub's push paths-ignore makes
    # (start and end of a push), so a push whose net change is docs-only never strands a code
    # commit. Different code on main (newer, or a revert of <tag>) means another CI deploys.
    on_main = git("merge-base", "--is-ancestor", tag, head, check=False).returncode
    if on_main not in (0, 1):
        raise SystemExit(f"git merge-base failed for {tag} and {head}")
    if on_main == 1:
        return False
    same = git("diff", "--quiet", tag, head, "--", *code_spec(), check=False).returncode
    if same not in (0, 1):
        raise SystemExit(f"git diff failed between {tag} and {head}")
    return same == 0


def is_heavy(base: str, head: str) -> bool:
    names = git("diff", "--name-only", "-z", f"{base}...{head}").stdout.decode("utf-8").split("\0")
    return any(n.startswith(HEAVY) for n in names if n)


def main(argv: list[str]) -> int:
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
