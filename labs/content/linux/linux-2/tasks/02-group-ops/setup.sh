#!/bin/sh
# Task 02: dev1 exists and belongs to the group webdev; the group ops does not exist yet.
# Runs as root; safe to run again (it also undoes a changed primary group of dev1).
getent group webdev >/dev/null || groupadd webdev || exit 1
getent passwd dev1 >/dev/null || useradd -m -s /bin/bash dev1 || exit 1
getent group dev1 >/dev/null || groupadd dev1 || exit 1
usermod -g dev1 dev1 >/dev/null 2>&1 || exit 1
groupdel ops >/dev/null 2>&1
usermod -G webdev dev1 >/dev/null 2>&1 || exit 1
exit 0
