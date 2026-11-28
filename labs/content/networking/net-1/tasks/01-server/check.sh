#!/bin/sh
# Task 01: a server on 8080 that serves ~/site.
# Root has no DAC override here (D45), so the student's files are read as the student, with fixed
# readers that only print file content; root parses it and decides. Nothing is changed.
have() { su student -s /bin/sh -c "test -f /home/student/$1" </dev/null; }
rd() { su student -s /bin/sh -c "head -c 65536 /home/student/$1 2>/dev/null" </dev/null; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
rc=0
if awk '$4=="0A" && $2 ~ /:1F90$/ {f=1} END{exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null \
  || nc -z -w 1 127.0.0.1 8080 </dev/null >/dev/null 2>&1; then
  pass 1
else
  fail 1 "nothing listens on port 8080; start the server with python3 -m http.server 8080 and leave it running (add & at the end)"
fi
page=$(curl -s -m 3 http://127.0.0.1:8080/index.html 2>/dev/null)
if printf '%s' "$page" | grep -q 'Net 1 practice site'; then
  pass 2
else
  fail 2 "port 8080 does not serve the site folder: the server may serve another folder or not answer; stop it, go into ~/site with cd and start it again from there"
fi
exit $rc
