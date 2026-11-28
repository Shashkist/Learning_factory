#!/bin/sh
# Task 03: the response headers of / in ~/headers.txt, and nothing else.
# Root has no DAC override here (D45), so the student's files are read as the student, with fixed
# readers that only print file content; root parses it and decides. Nothing is changed.
have() { su student -s /bin/sh -c "test -f /home/student/$1" </dev/null; }
rd() { su student -s /bin/sh -c "head -c 65536 /home/student/$1 2>/dev/null" </dev/null; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
rc=0
live=$(curl -sI -m 3 http://127.0.0.1:8080/ 2>/dev/null | tr -d '\r')
len=$(printf '%s\n' "$live" | sed -n 's/^[Cc]ontent-[Ll]ength: *//p' | head -n 1)
h=$(rd headers.txt | tr -d '\r')
if have headers.txt; then
  exists=1
else
  exists=0
fi
missing="the file ~/headers.txt does not exist; save the headers into it with > (curl -I URL asks for the headers)"

if [ "$exists" = 0 ]; then
  fail 1 "$missing"
elif printf '%s\n' "$h" | head -n 1 | grep -Eq '^HTTP/[0-9.]+ 200( |$)'; then
  pass 1
else
  fail 1 "headers.txt must start with the status line of the answer for / (HTTP/1.0 200 OK); ask for the page / and keep the headers"
fi

if [ "$exists" = 0 ]; then
  fail 2 "$missing"
elif printf '%s\n' "$h" | grep -Eiq '^content-type: *text/html'; then
  pass 2
else
  fail 2 "headers.txt has no Content-Type: text/html line; the headers must be those of the home page /"
fi

if [ "$exists" = 0 ]; then
  fail 3 "$missing"
elif [ -z "$len" ]; then
  fail 3 "the web server on port 8080 does not answer; restart this task so the server runs again"
elif printf '%s\n' "$h" | grep -Eiq "^content-length: *$len *\$"; then
  pass 3
else
  fail 3 "headers.txt has no Content-Length line that matches the home page; use the headers of / itself"
fi

if [ "$exists" = 0 ]; then
  fail 4 "$missing"
elif [ -n "$h" ] && printf '%s\n' "$h" | awk '
  !b && NF && $0 !~ /^(HTTP\/[0-9.]+ [0-9]+|[A-Za-z0-9-]+:)/ { bad = 1 }
  b && NF { bad = 1 }
  !NF { b = 1 }
  END { exit bad }'; then
  pass 4
else
  fail 4 "headers.txt must hold only headers: no page body after them (curl -I, or -D file with -o /dev/null; not -i)"
fi
exit $rc
