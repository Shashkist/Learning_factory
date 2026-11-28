#!/bin/sh
# Task 04: the Content-Type value of /data.json in ~/content-type.txt.
# Root has no DAC override here (D45), so the student's files are read as the student, with fixed
# readers that only print file content; root parses it and decides. Nothing is changed.
have() { su student -s /bin/sh -c "test -f /home/student/$1" </dev/null; }
rd() { su student -s /bin/sh -c "head -c 65536 /home/student/$1 2>/dev/null" </dev/null; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
rc=0
v=$(rd content-type.txt | tr -d '\r')
if have content-type.txt; then
  exists=1
else
  exists=0
fi
missing="the file ~/content-type.txt does not exist; save the value into it with >"

if [ "$exists" = 1 ]; then
  pass 1
else
  fail 1 "$missing"
fi

if [ "$exists" = 0 ]; then
  fail 2 "$missing"
elif printf '%s\n' "$v" | grep -qi 'application/json'; then
  pass 2
else
  fail 2 "the file does not say the content type of /data.json; read its headers with curl -I and look at Content-Type"
fi

if [ "$exists" = 0 ]; then
  fail 3 "$missing"
else
  set -f
  set -- $v
  set +f
  low=$(printf '%s' "$1" | tr 'A-Z' 'a-z')
  if [ $# -eq 1 ] && [ "$low" = application/json ]; then
    pass 3
  else
    fail 3 "write only the value (the part after Content-Type:), on its own, not the whole header line"
  fi
fi
exit $rc
