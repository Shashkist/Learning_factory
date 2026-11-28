#!/bin/sh
# Task 05 setup: the site, reply.json removed, and the echo service on 127.0.0.1:9090 (IPv4 only, as student, detached).
# The practice site; the same bytes at every run.
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
mkdir -p site
cat > site/index.html <<HTML
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Net 1 practice site</title></head>
<body>
<h1>Net 1 practice site</h1>
<p>If you can read this page, your web server works.</p>
</body>
</html>
HTML
cat > site/about.html <<HTML
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>About</title></head>
<body><p>A tiny site for the networking lab.</p></body>
</html>
HTML
cat > site/data.json <<JSON
{"lab": "net-1", "items": ["alpha", "beta", "gamma"]}
JSON
rm -f reply.json
' </dev/null
# listening PORT_HEX ADDR_HEX: a socket in the LISTEN state on that local address and port
listening() { awk -v want="$2:$1" '$4=="0A" && $2 == want {f=1} END{exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null; }
# The service source goes to a fresh root-owned directory (an unpredictable name, so nothing planted
# in /tmp can take its place), world-readable; the process runs as the student.
SRCDIR=$(mktemp -d) || { echo "cannot create a directory for the service" >&2; exit 1; }
trap 'rm -rf "$SRCDIR"' EXIT
chmod 0755 "$SRCDIR" || exit 1
cat > "$SRCDIR/echo.py" <<'PY'
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_ID = uuid.uuid4().hex


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(405, {"error": "use POST /echo"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size)
        if self.path != "/echo":
            return self._send(404, {"error": "not found"})
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            return self._send(415, {"error": "send the header Content-Type: application/json"})
        try:
            body = json.loads(raw)
        except ValueError:
            return self._send(400, {"error": "the body is not valid JSON"})
        self._send(200, {"id": SERVICE_ID, "echo": body})

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", 9090), Handler).serve_forever()
PY
chmod 0644 "$SRCDIR/echo.py" || exit 1
# 9090 is 2382 in hex, 127.0.0.1 is 0100007F
if ! listening 2382 0100007F; then
  su student -s /bin/sh -c '
setsid nohup python3 '"$SRCDIR"'/echo.py </dev/null >/dev/null 2>&1 &
' </dev/null
fi
i=0
until curl -s -m 1 -o /dev/null http://127.0.0.1:9090/ || [ "$i" -ge 40 ]; do
  i=$((i + 1))
  sleep 0.25
done
if [ "$i" -ge 40 ]; then echo "the service did not start" >&2; exit 1; fi
exit 0
