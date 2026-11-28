#!/bin/sh
# Task 01: the user dev1 does not exist yet. Runs as root; safe to run again.
userdel -f dev1 >/dev/null 2>&1
groupdel dev1 >/dev/null 2>&1
rm -rf /home/dev1
exit 0
