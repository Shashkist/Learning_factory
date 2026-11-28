#!/bin/sh
# Task 02: the group ops, dev1 as a listed member of it, and dev1 still in webdev. State only.
# A secondary group shows as a member in the group's own member list; a primary group does not.
member() {
  getent group "$1" | cut -d: -f4 | tr ',' '\n' | grep -qx "$2"
}
rc=0

if getent group ops >/dev/null; then
  echo "PASS 1"
else
  echo "FAIL 1 the group ops does not exist yet; create it with groupadd"
  rc=1
fi

if ! getent passwd dev1 >/dev/null; then
  echo "FAIL 2 the user dev1 does not exist; run the setup of this task again with Restart"
  echo "FAIL 3 the user dev1 does not exist; run the setup of this task again with Restart"
  exit 1
fi

if member ops dev1; then
  echo "PASS 2"
else
  echo "FAIL 2 dev1 is not a secondary member of ops yet; change the user with usermod, then look at id dev1"
  rc=1
fi

if member webdev dev1; then
  echo "PASS 3"
else
  echo "FAIL 3 dev1 lost its old group webdev; usermod -G replaces all the groups of a user, so use the -a option too (usermod -aG ops dev1) and put dev1 back with usermod -aG webdev dev1"
  rc=1
fi
exit $rc
