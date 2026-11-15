#!/bin/sh
f=/home/student/team-notes.txt
rc=0

if [ -f "$f" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/team-notes.txt is missing; do not delete it, only change its group"
  rc=1
fi

if [ -f "$f" ] && [ "$(stat -c %G "$f" 2>/dev/null)" = "team" ]; then
  echo "PASS 2"
else
  echo "FAIL 2 the group of team-notes.txt is not team yet; look at the group column of ls -l"
  rc=1
fi

if [ -f "$f" ] && [ "$(stat -c %U "$f" 2>/dev/null)" = "student" ]; then
  echo "PASS 3"
else
  echo "FAIL 3 the owner of team-notes.txt is not student; only the group should change"
  rc=1
fi

exit $rc
