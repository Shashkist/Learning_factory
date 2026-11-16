#!/bin/sh
# Task 04: copy report.txt to ~/backup, move photo.txt to ~/pictures.
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
f bak /home/student/backup/report.txt
c bak /home/student/backup/report.txt
f inrep /home/student/inbox/report.txt
f inpic /home/student/inbox/photo.txt
f pic /home/student/pictures/photo.txt
c pic /home/student/pictures/photo.txt
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

fact bak
if [ "$ty" = f ]; then
  if body bak | grep -qF "Quarterly report draft"; then
    echo "PASS 1"
  elif rd bak; then
    echo "FAIL 1 the file in ~/backup is not a copy of report.txt; which command makes a copy and leaves the original?"
    rc=1
  else
    echo "FAIL 1 the file ~/backup/report.txt cannot be read; check its permissions with ls -l"
    rc=1
  fi
else
  echo "FAIL 1 there is no copy of report.txt in ~/backup; which command makes a copy and leaves the original?"
  rc=1
fi

fact inrep
if [ "$ty" = f ]; then
  echo "PASS 2"
else
  echo "FAIL 2 report.txt is gone from ~/inbox; you needed a copy, not a move. Restart the task and try again"
  rc=1
fi

fact inpic; inpic=$ty
fact pic
if [ "$ty" != f ] || [ "$inpic" != - ]; then
  echo "FAIL 3 photo.txt must end up in ~/pictures and no longer be in ~/inbox; which command moves a file?"
  rc=1
elif ! rd pic; then
  echo "FAIL 3 the file ~/pictures/photo.txt cannot be read; check its permissions with ls -l"
  rc=1
elif body pic | grep -qF "Photo of the team"; then
  echo "PASS 3"
else
  echo "FAIL 3 ~/pictures/photo.txt is not the photo from ~/inbox: move the original with mv"
  rc=1
fi
exit $rc
