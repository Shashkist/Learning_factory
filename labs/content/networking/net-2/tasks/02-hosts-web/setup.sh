#!/bin/sh
# Task 02: web.lab is not in /etc/hosts, ~/web-reply.txt is gone, and ONE web service runs as student
# on 127.0.0.1:8081, serving /srv/lab-web with a fresh random token. The web root is root-owned:
# the student cannot change the page. Runs as root; safe to run again.
# /etc/hosts is a bind mount: it is rewritten in place, never replaced by a rename.
tmp=$(mktemp) || exit 1
# Remove only the name web.lab (any case). A line is dropped when no name is left on it,
# or when the name is only in a comment. Other lines stay as they are.
awk '{
  orig = $0
  c = index($0, "#"); cm = (c ? substr($0, c) : "")
  if (c && tolower(cm) ~ /web\.lab/) next
  $0 = (c ? substr(orig, 1, c - 1) : orig)
  hit = 0; kept = ""; nk = 0
  for (i = 2; i <= NF; i++) {
    if (tolower($i) == "web.lab") hit = 1
    else { kept = kept " " $i; nk++ }
  }
  if (!hit) print orig
  else if (nk > 0) print $1 kept
}' /etc/hosts > "$tmp"
cat "$tmp" > /etc/hosts || { rm -f "$tmp"; exit 1; }
rm -f "$tmp"

webroot=/srv/lab-web
mkdir -p "$webroot" || exit 1
chmod 755 "$webroot"

# Everything in the home folder is done by student (root has no right to write there).
su student -s /bin/sh -c '
  rm -f /home/student/web-reply.txt
  pkill -f "python3 -m [h]ttp.server 8081" >/dev/null 2>&1
  n=0
  while pgrep -f "python3 -m [h]ttp.server 8081" >/dev/null 2>&1 && [ "$n" -lt 25 ]; do
    sleep 0.2; n=$((n + 1))
  done
  exit 0
' || exit 1

token=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d " \n")
printf 'lab-web %s\n' "$token" > "$webroot/index.html" || exit 1
chmod 644 "$webroot/index.html"

su student -s /bin/sh -c '
  cd /srv/lab-web || exit 1
  mod=http.server  # not spelled in one piece with the command: pkill -f above must not match this shell
  setsid nohup python3 -m "$mod" 8081 --bind 127.0.0.1 >/dev/null 2>&1 </dev/null &
  exit 0
' || exit 1

n=0
while [ "$n" -lt 50 ]; do
  curl -s -m 2 -o /dev/null http://127.0.0.1:8081/ && exit 0
  sleep 0.2
  n=$((n + 1))
done
echo "the web service did not start" >&2
exit 1
