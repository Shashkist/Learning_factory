#!/bin/sh
# Task 02 setup: the site, the student's server brought up on 8080, and the answer files removed.
# Stop the student's web servers (as the student: root has no CAP_KILL here), then wait until 8080 is free.
su student -s /bin/sh -c "pkill -f '[h]ttp\.server'; exit 0" </dev/null >/dev/null 2>&1
i=0
while awk '$4=="0A" && $2 ~ /:1F90$/ {f=1} END{exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null && [ "$i" -lt 40 ]; do
  i=$((i + 1))
  sleep 0.25
done
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
rm -f status-ok.txt status-missing.txt
' </dev/null
# Bring the server up on 8080, serving ~/site, as the student, fully detached.
su student -s /bin/sh -c '
cd /home/student/site && setsid nohup python3 -m http.server 8080 --bind 127.0.0.1 </dev/null >/dev/null 2>&1 &
' </dev/null
i=0
until curl -s -m 1 -o /dev/null http://127.0.0.1:8080/index.html || [ "$i" -ge 40 ]; do
  i=$((i + 1))
  sleep 0.25
done
[ "$i" -lt 40 ]
exit 0
