#!/bin/sh
# Runs as student in one su call: root has no DAC override here (D45), so a private home
# (chmod 700 ~) would hide the student's files from root.
body=$(cat <<'EOF'
base=/home/student/projects/website
rc=0

if [ -d "$base" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 there is no folder ~/projects/website yet; try mkdir with the full path"
  rc=1
fi

if [ -d "$base/css" ]; then
  echo "PASS 2"
else
  echo "FAIL 2 there is no folder css inside website; check the spelling and where it is"
  rc=1
fi

if [ -d "$base/images" ]; then
  echo "PASS 3"
else
  echo "FAIL 3 there is no folder images inside website; check the spelling and where it is"
  rc=1
fi

exit $rc
EOF
)
if [ "$(id -u)" -eq 0 ]; then
  exec su student -s /bin/sh -c "$body" </dev/null
fi
exec /bin/sh -c "$body"
