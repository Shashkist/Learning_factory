#!/bin/sh
# Runs as student in one su call: root has no DAC override here (D45), so private folders
# (chmod 700) would hide the student's files from root.
body=$(cat <<'EOF'
h=/home/student
rc=0

if [ -f "$h/backup/report.txt" ]; then
  if cat "$h/backup/report.txt" 2>/dev/null | grep -qF "Quarterly report draft"; then
    echo "PASS 1"
  elif [ -r "$h/backup/report.txt" ]; then
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

if [ -f "$h/pictures/photo.txt" ] && [ ! -e "$h/inbox/photo.txt" ] \
   && cat "$h/pictures/photo.txt" 2>/dev/null | grep -qF "Photo of the team"; then
  echo "PASS 3"
else
  echo "FAIL 3 photo.txt must end up in ~/pictures and no longer be in ~/inbox; which command moves a file?"
  rc=1
fi

exit $rc
EOF
)
if [ "$(id -u)" -eq 0 ]; then
  exec su student -s /bin/sh -c "$body" </dev/null
fi
exec /bin/sh -c "$body"
