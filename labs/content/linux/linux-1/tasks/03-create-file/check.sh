#!/bin/sh
f=/home/student/notes/todo.txt
rc=0

if [ -f "$f" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/notes/todo.txt does not exist; make sure the name and the folder are right"
  rc=1
fi

if [ -f "$f" ] && grep -qi linux "$f" 2>/dev/null; then
  echo "PASS 2"
else
  echo "FAIL 2 the file does not contain the word linux; look at what you wrote into it with cat"
  rc=1
fi

lines=0
[ -f "$f" ] && lines=$(grep -c '' "$f" 2>/dev/null)
if [ "${lines:-0}" -ge 2 ]; then
  echo "PASS 3"
else
  echo "FAIL 3 the file has fewer than two lines; one > replaces the file, two >> add a line at the end"
  rc=1
fi

exit $rc
