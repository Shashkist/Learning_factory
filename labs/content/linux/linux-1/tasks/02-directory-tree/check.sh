#!/bin/sh
# Task 02: the folder tree under ~/projects/website.
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
f base /home/student/projects/website
f css /home/student/projects/website/css
f images /home/student/projects/website/images
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

fact base
if [ "$ty" = d ]; then
  echo "PASS 1"
else
  echo "FAIL 1 there is no folder ~/projects/website yet; try mkdir with the full path"
  rc=1
fi

fact css
if [ "$ty" = d ]; then
  echo "PASS 2"
else
  echo "FAIL 2 there is no folder css inside website; check the spelling and where it is"
  rc=1
fi

fact images
if [ "$ty" = d ]; then
  echo "PASS 3"
else
  echo "FAIL 3 there is no folder images inside website; check the spelling and where it is"
  rc=1
fi
exit $rc
