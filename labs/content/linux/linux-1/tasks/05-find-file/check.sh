#!/bin/sh
# Task 05: the full path of secret-recipe.txt in ~/found.txt.
# Root has no DAC override here (D45), so a private home would hide the student's files from it.
# One su call runs a fixed reader, piped on stdin, that only prints facts about the paths the task
# looks at. Root parses the facts and decides; no answer, hint or comparison reaches a student
# process, command line or file (D52).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
# f key path: type, mode, owner, group. c key path: readable flag and the lines.
f() {
  if [ -d "$2" ]; then t=d; elif [ -f "$2" ]; then t=f; elif [ -e "$2" ]; then t=o
  else printf '%s\n' "F $1 - - - -"; return; fi
  printf '%s\n' "F $1 $t $(stat -c '%a %U %G' "$2" 2>/dev/null || echo '- - -')"
}
c() {
  if [ -f "$2" ] && ( : < "$2" ) 2>/dev/null; then
    printf '%s\n' "R $1 1"
    head -c 65536 "$2" | head -n 200 | while IFS= read -r l || [ -n "$l" ]; do
      printf '%s\n' "C $1 $l"
    done
  else
    printf '%s\n' "R $1 0"
  fi
}
f found /home/student/found.txt
c found /home/student/found.txt
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
# tally LINES: reads the lines from a here-document, prints "non-empty-lines lines-equal-to-want other-lines". A builtin
# loop, so the expected path is never an argument of an external command (D52).
tally() {
  t=0; m=0; o=0
  while IFS= read -r l; do
    [ -n "$l" ] || continue
    t=$((t + 1))
    if [ "$l" = "$want" ]; then m=$((m + 1)); else o=$((o + 1)); fi
  done <<EOF
$1
EOF
  echo "$t $m $o"
}
rc=0

want=/home/student/archive/2023/spring/misc/secret-recipe.txt

fact found
if [ "$ty" = f ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/found.txt does not exist; send the output of your command into it with >"
  rc=1
fi

if [ "$ty" = f ]; then
  if rd found; then
    set -- $(tally "$(body found)")
    n=$1; right=$2; other=$3
    if [ "$right" -ge 1 ] && [ "$n" -eq 1 ]; then
      echo "PASS 2"
    elif [ "$n" -gt 1 ] && [ "$other" -eq 0 ]; then
      echo "FAIL 2 found.txt holds the right path more than once (duplicate lines), probably from running >> twice; > replaces the file, so run it again with a single >"
      rc=1
    elif [ "$n" -gt 1 ]; then
      echo "FAIL 2 found.txt lists more than one path; search for the exact name secret-recipe.txt so that only one file matches"
      rc=1
    else
      echo "FAIL 2 found.txt does not hold the full path of secret-recipe.txt; a full path starts with /home/student"
      rc=1
    fi
  else
    echo "FAIL 2 the file ~/found.txt cannot be read; check its permissions with ls -l"
    rc=1
  fi
else
  echo "FAIL 2 found.txt does not hold the full path of secret-recipe.txt; a full path starts with /home/student"
  rc=1
fi
exit $rc
