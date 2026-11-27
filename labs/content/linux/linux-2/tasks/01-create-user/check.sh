#!/bin/sh
# Task 01: the account dev1, its home folder and its shell. State only (getent, stat).
entry=$(getent passwd dev1)
home=$(printf '%s\n' "$entry" | cut -d: -f6)
shell=$(printf '%s\n' "$entry" | cut -d: -f7)
rc=0

if [ -n "$entry" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the user dev1 does not exist yet; create it with useradd"
  rc=1
fi

if [ -z "$entry" ]; then
  echo "FAIL 2 there is no user dev1 yet, so there is no home folder; create the user first"
  rc=1
elif [ ! -d /home/dev1 ]; then
  echo "FAIL 2 the folder /home/dev1 does not exist; useradd creates a home folder only with the -m option"
  rc=1
elif [ "$home" != /home/dev1 ]; then
  echo "FAIL 2 the home folder of dev1 is $home but it should be /home/dev1"
  rc=1
elif [ "$(stat -c %U /home/dev1 2>/dev/null)" != dev1 ]; then
  echo "FAIL 2 /home/dev1 exists but it does not belong to dev1; let useradd -m create it"
  rc=1
else
  echo "PASS 2"
fi

if [ -z "$entry" ]; then
  echo "FAIL 3 there is no user dev1 yet, so there is no shell to check; create the user first"
  rc=1
elif [ "$shell" = /bin/bash ]; then
  echo "PASS 3"
else
  echo "FAIL 3 the login shell of dev1 is ${shell:-empty}, not /bin/bash; set it with useradd -s or chsh -s"
  rc=1
fi
exit $rc
