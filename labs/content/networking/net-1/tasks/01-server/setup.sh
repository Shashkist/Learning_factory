#!/bin/sh
# Task 01 setup: the practice site in ~/site, and port 8080 free (a server that runs is stopped).
# Stop the student's web servers (as the student: root has no CAP_KILL here), then wait until 8080 is free.
su student -s /bin/sh -c "pkill -f '[h]ttp\.server'; exit 0" </dev/null >/dev/null 2>&1
i=0
while awk '$4=="0A" && $2 ~ /:1F90$/ {f=1} END{exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null && [ "$i" -lt 40 ]; do
  i=$((i + 1))
  sleep 0.25
done
if awk '$4=="0A" && $2 ~ /:1F90$/ {f=1} END{exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null; then echo "port 8080 is busy with another program" >&2; exit 1; fi
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

' </dev/null
exit 0
