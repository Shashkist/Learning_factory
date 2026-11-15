#!/bin/sh
# Root cannot bypass file permissions here, so the student's file is read as student.
h=/home/student
rc=0

if [ -f "$h/backup/report.txt" ]; then
  if su student -s /bin/sh -c 'cat /home/student/backup/report.txt' </dev/null 2>/dev/null | grep -qF "Quarterly report draft"; then
    echo "PASS 1"
  elif su student -s /bin/sh -c 'test -r /home/student/backup/report.txt' </dev/null 2>/dev/null; then
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

if [ -f "$h/inbox/report.txt" ]; then
  echo "PASS 2"
else
  echo "FAIL 2 report.txt is gone from ~/inbox; you needed a copy, not a move. Restart the task and try again"
  rc=1
fi

if [ -f "$h/pictures/photo.txt" ] && [ ! -e "$h/inbox/photo.txt" ]; then
  echo "PASS 3"
else
  echo "FAIL 3 photo.txt must end up in ~/pictures and no longer be in ~/inbox; which command moves a file?"
  rc=1
fi

exit $rc
