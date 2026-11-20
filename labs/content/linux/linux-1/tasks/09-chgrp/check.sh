#!/bin/sh
# Task 09: owner and group of ~/team-notes.txt.
# Root has no DAC override here (D45), so a private home would hide the student's files from it.
# One su call runs a fixed reader, piped on stdin, that only prints facts about the paths the task
# looks at. Root parses the facts and decides; no answer, hint or comparison reaches a student
# process, command line or file (D52).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
# f key path: type, mode, owner, group.
f() {
  if [ -d "$2" ]; then t=d; elif [ -f "$2" ]; then t=f; elif [ -e "$2" ]; then t=o
  else printf '%s\n' "F $1 - - - -"; return; fi
  printf '%s\n' "F $1 $t $(stat -c '%a %U %G' "$2" 2>/dev/null || echo '- - -')"
}
f notes /home/student/team-notes.txt
READER
)
# Root side: fact sets ty mo ow gr for a key.
fact() {
  ty=-; mo=-; ow=-; gr=-
  set -f; set -- $(printf '%s\n' "$facts" | sed -n "s/^F $1 //p" | head -n 1); set +f
  if [ $# -eq 4 ]; then ty=$1; mo=$2; ow=$3; gr=$4; fi
  return 0
}
rc=0

fact notes
if [ "$ty" = f ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/team-notes.txt is missing; do not delete it, only change its group"
  rc=1
fi

if [ "$ty" = f ] && [ "$gr" = team ]; then
  echo "PASS 2"
else
  echo "FAIL 2 the group of team-notes.txt is not team yet; look at the group column of ls -l"
  rc=1
fi

if [ "$ty" = f ] && [ "$ow" = student ]; then
  echo "PASS 3"
else
  echo "FAIL 3 the owner of team-notes.txt is not student; only the group should change"
  rc=1
fi
exit $rc
