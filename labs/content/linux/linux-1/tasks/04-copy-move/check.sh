#!/bin/sh
h=/home/student
rc=0

if [ -f "$h/backup/report.txt" ] && grep -qF "Quarterly report draft" "$h/backup/report.txt" 2>/dev/null; then
  echo "PASS 1"
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
