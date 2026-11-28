#!/bin/sh
# Task 08: ~/pong.txt holds the reply of the service on 127.0.0.1:9091, as it comes live.
# Root has no DAC override here (D45), so the student's files are read as the student, with fixed
# readers that only print file content; root parses it and decides. Nothing is changed.
have() { su student -s /bin/sh -c "test -f /home/student/$1" </dev/null; }
rd() { su student -s /bin/sh -c "head -c 65536 /home/student/$1 2>/dev/null" </dev/null; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
rc=0
live=$(curl -s -m 3 http://127.0.0.1:9091/ 2>/dev/null | tr -d '\r')
set -f
set -- $live
set +f
if [ $# -eq 1 ]; then
  want=$1
else
  want=
fi
if have pong.txt; then
  exists=1
else
  exists=0
fi
set -f
set -- $(rd pong.txt | tr -d '\r')
set +f
words=$#
got=$*
lead="the address 127.0.0.1 and port 9091 are exact; the name localhost may lead to another address (IPv6 ::1) and another program, so ask 127.0.0.1"

if [ "$exists" = 1 ]; then
  pass 1
else
  fail 1 "the file ~/pong.txt does not exist; save the answer of the service into it with >"
fi

if [ "$exists" = 0 ]; then
  fail 2 "the file ~/pong.txt does not exist; save the answer of the service into it with >"
elif [ "$words" -eq 1 ] && printf '%s' "$got" | grep -Eq '^pong:[0-9a-f]+$'; then
  pass 2
else
  fail 2 "pong.txt must hold one reply of the form pong:<token> exactly as the service sent it; $lead"
fi

if [ "$exists" = 0 ]; then
  fail 3 "the file ~/pong.txt does not exist; save the answer of the service into it with >"
elif [ -z "$want" ]; then
  fail 3 "the service on 127.0.0.1:9091 does not answer; restart this task so the service runs again"
elif [ "$got" = "$want" ]; then
  pass 3
else
  fail 3 "pong.txt does not hold the reply of the service at 127.0.0.1 port 9091; $lead"
fi
exit $rc
