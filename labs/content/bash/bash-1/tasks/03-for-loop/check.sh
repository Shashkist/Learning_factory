#!/bin/sh
# Task 03: ~/files holds exactly the regular files file1 .. file5.
# State only: the check does not care how the files were made. Root has no DAC override here
# (D45), so one su call runs a fixed reader, piped on stdin, that only prints facts about the
# paths the task looks at. Root parses the facts and decides (D52).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
f=/home/student/files
if [ -d "$f" ] && [ ! -L "$f" ]; then echo "D 1"; else echo "D 0"; fi
for i in 1 2 3 4 5; do
  if [ -f "$f/file$i" ] && [ ! -L "$f/file$i" ]; then echo "F $i 1"; else echo "F $i 0"; fi
done
echo "E $(ls -A "$f" 2>/dev/null | grep -cvxE 'file[1-5]')"
READER
)
rc=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }

isdir=$(printf '%s\n' "$facts" | sed -n 's/^D //p' | head -n 1)
if [ "$isdir" = 1 ]; then
  pass 1
else
  fail 1 "the folder ~/files does not exist; create it with mkdir ~/files"
fi

have=$(printf '%s\n' "$facts" | grep -c '^F [1-5] 1$')
if [ "$isdir" = 1 ] && [ "$have" -eq 5 ]; then
  pass 2
else
  fail 2 "~/files must hold the files file1, file2, file3, file4 and file5 (regular files, $have of 5 found); a for loop with touch makes them"
fi

extra=$(printf '%s\n' "$facts" | sed -n 's/^E //p' | head -n 1)
if [ "$isdir" = 1 ] && [ "$extra" = 0 ]; then
  pass 3
else
  fail 3 "~/files holds something besides file1 to file5, or does not exist; look with ls -A ~/files and remove what does not belong"
fi
exit $rc
