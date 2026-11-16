#!/bin/sh
# Runs as student in one su call: root has no DAC override here (D45), so a private home
# (chmod 700 ~) would hide the student's files from root.
body=$(cat <<'EOF'
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
  if content=$(cat "$f" 2>/dev/null); then
    n=$(printf '%s\n' "$content" | grep -c .)
    other=$(printf '%s\n' "$content" | grep . | grep -cvxF "$want")
    if printf '%s\n' "$content" | grep -qxF "$want" && [ "$n" -eq 1 ]; then
      echo "PASS 2"
    elif [ "$n" -gt 1 ] && [ "$other" -eq 0 ]; then
      echo "FAIL 2 found.txt holds the right path more than once (duplicate lines), probably from running >> twice; > replaces the file, so run it again with a single >"
      rc=1
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
EOF
)
if [ "$(id -u)" -eq 0 ]; then
  exec su student -s /bin/sh -c "$body" </dev/null
fi
exec /bin/sh -c "$body"
