#!/bin/sh
# Runs as root, so it refers to the student's home by absolute path.
f=/home/student/hello.txt
rc=0

if [ -f "$f" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file /home/student/hello.txt does not exist"
  rc=1
fi

if [ -f "$f" ] && grep -q hello "$f"; then
  echo "PASS 2"
else
  echo "FAIL 2 the file does not contain the word hello"
  rc=1
fi

exit $rc
