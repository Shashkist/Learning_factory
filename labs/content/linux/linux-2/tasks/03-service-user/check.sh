#!/bin/sh
# Task 03: the account svc-app, its shell and the absence of a home folder. State only.
entry=$(getent passwd svc-app)
home=$(printf '%s\n' "$entry" | cut -d: -f6)
shell=$(printf '%s\n' "$entry" | cut -d: -f7)
rc=0

if [ -n "$entry" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the user svc-app does not exist yet; create it with useradd"
  rc=1
fi

if [ -z "$entry" ]; then
  echo "FAIL 2 there is no user svc-app yet, so there is no shell to check; create the user first"
  rc=1
elif [ "$shell" = /usr/sbin/nologin ]; then
  echo "PASS 2"
else
  echo "FAIL 2 the shell of svc-app is ${shell:-empty}, not /usr/sbin/nologin; set it with useradd -s or usermod -s"
  rc=1
fi

if [ -z "$entry" ]; then
  echo "FAIL 3 there is no user svc-app yet, so create the user first, without a home folder"
  rc=1
elif [ -e /home/svc-app ] || { [ -n "$home" ] && [ -e "$home" ]; }; then
  echo "FAIL 3 svc-app has a home folder (${home:-/home/svc-app}); create the user without one (useradd -M or -r) or delete the folder"
  rc=1
else
  echo "PASS 3"
fi
exit $rc
