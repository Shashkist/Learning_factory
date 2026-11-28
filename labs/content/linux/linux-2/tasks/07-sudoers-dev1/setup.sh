#!/bin/sh
# Task 07: dev1 exists and has no rule file of its own. Runs as root; safe to run again.
getent passwd dev1 >/dev/null || useradd -m -s /bin/bash dev1 || exit 1
rm -f /etc/sudoers.d/dev1 /etc/sudoers.d/dev1-*
exit 0
