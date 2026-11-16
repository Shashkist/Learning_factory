#!/bin/sh
# Runs as student in one su call: root has no DAC override here (D45), so a private home
# (chmod 700 ~) or notes folder would hide the student's files from root.
body=$(cat <<'EOF'
f=/home/student/notes/todo.txt
rc=0

if [ -f "$f" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file ~/notes/todo.txt does not exist; make sure the name and the folder are right"
  rc=1
fi

first=""
lines=0
readable=0
if [ -f "$f" ]; then
  if first=$(head -n 1 "$f" 2>/dev/null); then
    readable=1
    lines=$(grep -c "" "$f" 2>/dev/null)
  fi
fi

if [ -f "$f" ] && [ "$readable" -eq 0 ]; then
  echo "FAIL 2 the file ~/notes/todo.txt cannot be read; check its permissions with ls -l"
  echo "FAIL 3 the file ~/notes/todo.txt cannot be read; check its permissions with ls -l"
  exit 1
fi

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
EOF
)
if [ "$(id -u)" -eq 0 ]; then
  exec su student -s /bin/sh -c "$body" </dev/null
fi
exec /bin/sh -c "$body"
