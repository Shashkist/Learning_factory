#!/bin/sh
# The expected lines are kept here, so the check does not depend on the student's copy of the log.
out=/home/student/errors.txt
exp=$(mktemp) || exit 1
trap 'rm -f "$exp"' EXIT
cat > "$exp" <<LOG
2025-11-14 09:01:12 ERROR cannot open file /data/report.csv
2025-11-14 09:03:15 ERROR connection to database lost
2025-11-14 09:04:09 ERROR retry failed
2025-11-14 09:06:11 ERROR timeout while saving profile
LOG
rc=0

if [ -f "$out" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/errors.txt does not exist; send the output of grep into it with >"
  rc=1
fi

got=$(mktemp) || exit 1
trap 'rm -f "$exp" "$got"' EXIT
if [ -f "$out" ] && ! su student -s /bin/sh -c 'cat /home/student/errors.txt' </dev/null > "$got" 2>/dev/null; then
  echo "FAIL 2 the file ~/errors.txt cannot be read; check its permissions with ls -l"
  echo "FAIL 3 the file ~/errors.txt cannot be read; check its permissions with ls -l"
  exit 1
fi

missing=4
[ -f "$out" ] && missing=$(grep -Fxv -f "$got" "$exp" 2>/dev/null | wc -l | tr -d ' ')
if [ "$missing" -eq 0 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 errors.txt misses $missing of the 4 ERROR lines; search the whole log, in capital letters"
  rc=1
fi

extra=1
[ -s "$got" ] && extra=$(grep -Fxv -f "$exp" "$got" 2>/dev/null | wc -l | tr -d ' ')
if [ "$extra" -eq 0 ]; then
  echo "PASS 3"
else
  echo "FAIL 3 errors.txt is empty or has lines that are not ERROR lines; grep should print only matching lines"
  rc=1
fi

exit $rc
