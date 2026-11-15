#!/bin/sh
# Root cannot bypass file permissions here, so the student's file is read as student.
f=/home/student/found.txt
want=/home/student/archive/2023/spring/misc/secret-recipe.txt
rc=0

if [ -f "$f" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/found.txt does not exist; send the output of your command into it with >"
  rc=1
fi

if [ -f "$f" ]; then
  if content=$(su student -s /bin/sh -c 'cat /home/student/found.txt' </dev/null 2>/dev/null); then
    n=$(printf '%s
' "$content" | grep -c .)
    if printf '%s
' "$content" | grep -qxF "$want" && [ "$n" -eq 1 ]; then
      echo "PASS 2"
    elif [ "$n" -gt 1 ]; then
      echo "FAIL 2 found.txt lists more than one path; search for the exact name secret-recipe.txt so that only one file matches"
      rc=1
    else
      echo "FAIL 2 found.txt does not hold the full path of secret-recipe.txt; a full path starts with /home/student"
      rc=1
    fi
  else
    echo "FAIL 2 the file ~/found.txt cannot be read; check its permissions with ls -l"
    rc=1
  fi
else
  echo "FAIL 2 found.txt does not hold the full path of secret-recipe.txt; a full path starts with /home/student"
  rc=1
fi

exit $rc
