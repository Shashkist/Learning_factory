#!/bin/sh
# Task 02: the status codes of /index.html and of a missing page, compared with what the live server says.
# Root has no DAC override here (D45), so the student's files are read as the student, with fixed
# readers that only print file content; root parses it and decides. Nothing is changed.
have() { su student -s /bin/sh -c "test -f /home/student/$1" </dev/null; }
rd() { su student -s /bin/sh -c "head -c 65536 /home/student/$1 2>/dev/null" </dev/null; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
rc=0
code() { curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080/$1" 2>/dev/null; }
# one NAME: prints the only word of the file (whitespace around it and a CR are ignored)
one() {
  set -f
  set -- $(rd "$1" | tr -d '\r')
  set +f
  [ $# -eq 1 ] && printf '%s' "$1"
}
live_ok=$(code index.html)
live_missing=$(code no-such-page-lf.html)
if [ "$live_ok" != 200 ] || [ "$live_missing" != 404 ]; then
  server=down
else
  server=up
fi

# item N FILE LIVE NAME: one answer file against the live status code
item() {
  if ! have "$2"; then
    fail "$1" "the file ~/$2 does not exist; save the status code into it with > (curl -s -o /dev/null -w '%{http_code}' URL)"
  elif [ "$server" = down ]; then
    fail "$1" "the web server on port 8080 does not answer as expected; restart this task so the server runs again"
  elif [ "$(one "$2")" = "$3" ]; then
    pass "$1"
  else
    fail "$1" "~/$2 must hold only the status code $4; curl -s -o /dev/null -w '%{http_code}' URL prints just the number"
  fi
}
item 1 status-ok.txt "$live_ok" "of the page /index.html"
item 2 status-missing.txt "$live_missing" "you get for a page that does not exist"
exit $rc
