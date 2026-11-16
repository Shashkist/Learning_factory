#!/bin/sh
# Task 03: ~/notes/todo.txt.
# Root has no DAC override here (D45), so a private home would hide the student's files from it.
# One su call runs a fixed reader, piped on stdin, that only prints facts about the paths the task
# looks at. Root parses the facts and decides; no answer, hint or comparison reaches a student
# process, command line or file (D52).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
# f key path: type, mode, owner, group. c key path: readable flag and the lines.
f() {
  if [ -d "$2" ]; then t=d; elif [ -f "$2" ]; then t=f; elif [ -e "$2" ]; then t=o
  else echo "F $1 - - - -"; return; fi
  echo "F $1 $t $(stat -c '%a %U %G' "$2" 2>/dev/null || echo '- - -')"
}
c() {
  if ( : < "$2" ) 2>/dev/null; then
    echo "R $1 1"
    head -n 200 "$2" | while IFS= read -r l || [ -n "$l" ]; do echo "C $1 $l"; done
  else
    echo "R $1 0"
  fi
}
f todo /home/student/notes/todo.txt
c todo /home/student/notes/todo.txt
READER
)
# Root side: fact sets ty mo ow gr for a key; rd tells whether the student could read it; body prints its lines.
fact() {
  ty=-; mo=-; ow=-; gr=-
  set -- $(printf '%s\n' "$facts" | sed -n "s/^F $1 //p" | head -n 1)
  if [ $# -eq 4 ]; then ty=$1; mo=$2; ow=$3; gr=$4; fi
  return 0
}
rd() { printf '%s\n' "$facts" | grep -qx "R $1 1"; }
body() { printf '%s\n' "$facts" | sed -n "s/^C $1 //p"; }
rc=0

fact todo
if [ "$ty" = f ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/notes/todo.txt does not exist; make sure the name and the folder are right"
  rc=1
fi

if [ "$ty" = f ] && ! rd todo; then
  echo "FAIL 2 the file ~/notes/todo.txt cannot be read; check its permissions with ls -l"
  echo "FAIL 3 the file ~/notes/todo.txt cannot be read; check its permissions with ls -l"
  exit 1
fi

first=$(body todo | head -n 1)
lines=$(body todo | grep -c "")

if printf '%s\n' "$first" | grep -qi linux; then
  echo "PASS 2"
else
  echo "FAIL 2 the first line does not contain the word linux; look at the file with cat, and remember that > replaces what was there"
  rc=1
fi

if [ "${lines:-0}" -ge 2 ]; then
  echo "PASS 3"
else
  echo "FAIL 3 the file has fewer than two lines; one > replaces the file, two >> add a line at the end"
  rc=1
fi
exit $rc
