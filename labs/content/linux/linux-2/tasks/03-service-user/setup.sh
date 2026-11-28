#!/bin/sh
# Task 03: the user svc-app does not exist yet. Runs as root; safe to run again.
userdel -f svc-app >/dev/null 2>&1
groupdel svc-app >/dev/null 2>&1
rm -rf /home/svc-app
exit 0
