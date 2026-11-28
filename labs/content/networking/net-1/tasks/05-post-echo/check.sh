#!/bin/sh
# Task 05: the reply of the echo service to the POST in ~/reply.json (valid JSON, its echo, its id).
# Root has no DAC override here (D45), so the student's files are read as the student, with fixed
# readers that only print file content; root parses it and decides. Nothing is changed.
have() { su student -s /bin/sh -c "test -f /home/student/$1" </dev/null; }
rd() { su student -s /bin/sh -c "head -c 65536 /home/student/$1 2>/dev/null" </dev/null; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
rc=0
BODY='{"lab":"net-1","msg":"hello"}'
live=$(curl -s -m 3 -X POST -H 'Content-Type: application/json' -d "$BODY" http://127.0.0.1:9090/echo 2>/dev/null)
if have reply.json; then
  exists=1
else
  exists=0
fi
facts=$(rd reply.json | python3 -c '
import json
import sys

body, live = json.loads(sys.argv[1]), sys.argv[2]
try:
    service_id = json.loads(live)["id"]
except Exception:
    service_id = None
try:
    reply = json.loads(sys.stdin.read())
except Exception:
    print("0 0 0")
    sys.exit()
ok = isinstance(reply, dict)
print(1, int(ok and reply.get("echo") == body), int(ok and service_id is not None and reply.get("id") == service_id))
' "$BODY" "$live" 2>/dev/null)
set -- $facts
valid=${1:-0}
echoed=${2:-0}
same=${3:-0}
missing="the file ~/reply.json does not exist; save the reply of the service into it with > or -o"

if [ "$exists" = 0 ]; then
  fail 1 "$missing"
elif [ "$valid" = 1 ]; then
  pass 1
else
  fail 1 "reply.json is not valid JSON; save only the reply of the service (curl -s ... > ~/reply.json)"
fi

if [ "$exists" = 0 ]; then
  fail 2 "$missing"
elif [ "$echoed" = 1 ]; then
  pass 2
else
  fail 2 "reply.json has no echo part that equals the body {\"lab\":\"net-1\",\"msg\":\"hello\"}; send exactly that body with the header Content-Type: application/json (an error reply means the header is missing)"
fi

if [ "$exists" = 0 ]; then
  fail 3 "$missing"
elif [ -z "$live" ]; then
  fail 3 "the echo service on 127.0.0.1:9090 does not answer; restart this task so the service runs again"
elif [ "$same" = 1 ]; then
  pass 3
else
  fail 3 "the id in reply.json is not the id of the running service; the reply must come from the service itself, not typed by hand"
fi
exit $rc
