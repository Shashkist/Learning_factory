#!/bin/sh
# Task 06: the ERROR lines of the log in ~/errors.txt.
# Root has no DAC override here (D45), so a private home would hide the student's files from it.
# One su call runs a fixed reader, piped on stdin, that only prints facts about the paths the task
# looks at. Root parses the facts and decides; no answer, hint or comparison reaches a student
# process, command line or file (D52).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
# f key path: type, mode, owner, group. c key path: readable flag, a truncation flag and the lines.
f() {
  if [ -d "$2" ]; then t=d; elif [ -f "$2" ]; then t=f; elif [ -e "$2" ]; then t=o
  else printf '%s\n' "F $1 - - - -"; return; fi
  printf '%s\n' "F $1 $t $(stat -c '%a %U %G' "$2" 2>/dev/null || echo '- - -')"
}
c() {
  if [ -f "$2" ] && ( : < "$2" ) 2>/dev/null; then
    printf '%s\n' "R $1 1"
    # Truncation fact: more than 64 KiB or more than 200 lines, so root knows it saw only a part.
    if [ "$(head -c 65537 "$2" | wc -c)" -gt 65536 ] || [ -n "$(head -c 65536 "$2" | sed -n '201p')" ]; then
      printf '%s\n' "T $1 1"
    fi
    head -c 65536 "$2" | head -n 200 | while IFS= read -r l || [ -n "$l" ]; do
      printf '%s\n' "C $1 $l"
    done
  else
    printf '%s\n' "R $1 0"
  fi
}
f out /home/student/errors.txt
c out /home/student/errors.txt
READER
)
# Root side: fact sets ty mo ow gr for a key; rd tells whether the student could read it; body prints its lines.
fact() {
  ty=-; mo=-; ow=-; gr=-
  set -f; set -- $(printf '%s\n' "$facts" | sed -n "s/^F $1 //p" | head -n 1); set +f
  if [ $# -eq 4 ]; then ty=$1; mo=$2; ow=$3; gr=$4; fi
  return 0
}
rd() { printf '%s\n' "$facts" | grep -qx "R $1 1"; }
body() { printf '%s\n' "$facts" | sed -n "s/^C $1 //p"; }
rc=0

# The expected lines are kept here, so the check does not depend on the student's copy of the log.
exp='2025-11-14 09:01:12 ERROR cannot open file /data/report.csv
2025-11-14 09:03:15 ERROR connection to database lost
2025-11-14 09:04:09 ERROR retry failed
2025-11-14 09:06:11 ERROR timeout while saving profile'

fact out
if [ "$ty" = f ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/errors.txt does not exist; send the output of grep into it with >"
  rc=1
fi

if [ "$ty" = f ] && ! rd out; then
  echo "FAIL 2 the file ~/errors.txt cannot be read; check its permissions with ls -l"
  echo "FAIL 3 the file ~/errors.txt cannot be read; check its permissions with ls -l"
  exit 1
fi

# Compare through a root-only folder (mode 700) so that neither the expected lines nor the
# student's lines are ever on a command line (D52).
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
printf '%s\n' "$exp" > "$tmp/exp"
body out > "$tmp/got"
missing=4
if [ "$ty" = f ]; then
  missing=$(grep -cvxF -f "$tmp/got" "$tmp/exp")
fi
if [ "$missing" -eq 0 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 errors.txt misses $missing of the 4 ERROR lines; search the whole log, in capital letters"
  rc=1
fi

extra=1
if [ "$ty" = f ] && [ -s "$tmp/got" ]; then
  extra=$(grep -cvxF -f "$tmp/exp" "$tmp/got")
fi
if [ "$ty" = f ] && printf '%s\n' "$facts" | grep -qx "T out 1"; then
  echo "FAIL 3 errors.txt is far longer than the ERROR lines of the log; grep should print only matching lines"
  rc=1
elif [ "$extra" -eq 0 ]; then
  echo "PASS 3"
else
  echo "FAIL 3 errors.txt is empty or has lines that are not ERROR lines; grep should print only matching lines"
  rc=1
fi
exit $rc
