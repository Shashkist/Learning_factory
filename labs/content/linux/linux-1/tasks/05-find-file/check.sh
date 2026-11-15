#!/bin/sh
f=/home/student/found.txt
want=/home/student/archive/2023/spring/misc/secret-recipe.txt
rc=0

if [ -f "$f" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/found.txt does not exist; send the output of your command into it with >"
  rc=1
fi

if [ -f "$f" ] && grep -qxF "$want" "$f" 2>/dev/null; then
  echo "PASS 2"
else
  echo "FAIL 2 found.txt does not hold the full path of secret-recipe.txt; a full path starts with /home/student"
  rc=1
fi

exit $rc
