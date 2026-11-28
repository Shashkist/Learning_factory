#!/bin/sh
# Task 08 setup: the site, pong.txt removed, the real service on 127.0.0.1:9091 (IPv4 only) and a placeholder page on [::1]:9091.
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
rm -f pong.txt
' </dev/null
# listening PORT_HEX ADDR_HEX: a socket in the LISTEN state on that local address and port
listening() { awk -v want="$2:$1" '$4=="0A" && $2 == want {f=1} END{exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null; }
# The service source goes to a fresh root-owned directory (an unpredictable name, so nothing planted
# in /tmp can take its place), world-readable; the processes run as the student.
SRCDIR=$(mktemp -d) || { echo "cannot create a directory for the service" >&2; exit 1; }
trap 'rm -rf "$SRCDIR"' EXIT
chmod 0755 "$SRCDIR" || exit 1
cat > "$SRCDIR/pong.py" <<'PY'
import socket
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REAL = sys.argv[1] == "real"
if REAL:
    BODY = ("pong:" + uuid.uuid4().hex[:12] + "\n").encode()
else:
    BODY = b"It works! This is the default page of this host.\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    address_family = socket.AF_INET if REAL else socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        if not REAL:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


Server(("127.0.0.1" if REAL else "::1", 9091), Handler).serve_forever()
PY
chmod 0644 "$SRCDIR/pong.py" || exit 1
# 9091 is 2383 in hex; 127.0.0.1 is 0100007F, ::1 is 00000000000000000000000001000000
if ! listening 2383 0100007F; then
  su student -s /bin/sh -c '
setsid nohup python3 '"$SRCDIR"'/pong.py real </dev/null >/dev/null 2>&1 &
' </dev/null
fi
if ! listening 2383 00000000000000000000000001000000; then
  su student -s /bin/sh -c '
setsid nohup python3 '"$SRCDIR"'/pong.py decoy </dev/null >/dev/null 2>&1 &
' </dev/null
fi
i=0
until { curl -s -m 1 -o /dev/null http://127.0.0.1:9091/ && curl -s -m 1 -g -o /dev/null "http://[::1]:9091/"; } || [ "$i" -ge 40 ]; do
  i=$((i + 1))
  sleep 0.25
done
if [ "$i" -ge 40 ]; then echo "the service did not start" >&2; exit 1; fi
exit 0
