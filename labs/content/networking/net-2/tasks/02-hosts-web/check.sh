#!/bin/sh
# Task 02: web.lab resolves to 127.0.0.1 through /etc/hosts, the web service answers on 8081,
# and ~/web-reply.txt holds the page that service serves now. State only.
# The first line of /etc/hosts that names web.lab decides (like the resolver does).
addr=$(sed 's/#.*//' /etc/hosts | awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ {for (i = 2; i <= NF; i++) if (tolower($i) == "web.lab") {print $1; exit}}')
page=$(curl -s -f -m 5 http://127.0.0.1:8081/ 2>/dev/null)
reply=$(su student -s /bin/sh -c 'cat /home/student/web-reply.txt' 2>/dev/null)
rc=0

if [ "$addr" = 127.0.0.1 ]; then
  echo "PASS 1"
else
  echo "FAIL 1 /etc/hosts has no line 127.0.0.1 web.lab (address first, then the name); changing /etc/hosts needs root, so use sudo, for example echo '127.0.0.1 web.lab' | sudo tee -a /etc/hosts"
  rc=1
fi

if [ -n "$page" ]; then
  echo "PASS 2"
else
  echo "FAIL 2 nothing answers on port 8081; the web service is not running, press Restart to start it again"
  rc=1
fi

if [ -n "$page" ] && [ "$reply" = "$page" ]; then
  echo "PASS 3"
else
  echo "FAIL 3 ~/web-reply.txt is missing or is not the page of the service; save it with curl -s (no -i) http://web.lab:8081/ > ~/web-reply.txt, after web.lab works"
  rc=1
fi
exit $rc
