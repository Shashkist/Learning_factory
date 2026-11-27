#!/bin/sh
# Task 04: olduser exists with a usable (unlocked) password. The password is a fake lab value.
# Runs as root; safe to run again (it unlocks the account and resets the shell).
getent passwd olduser >/dev/null || useradd -m -s /bin/bash olduser || exit 1
usermod -s /bin/bash olduser >/dev/null 2>&1 || exit 1
echo 'olduser:lab-only-pass-1' | chpasswd || exit 1
usermod -U olduser >/dev/null 2>&1
exit 0
