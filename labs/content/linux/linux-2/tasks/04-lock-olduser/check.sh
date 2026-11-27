#!/bin/sh
# Task 04: olduser still exists and its account is locked. State only (passwd -S).
rc=0
if getent passwd olduser >/dev/null; then
  echo "PASS 1"
else
  echo "FAIL 1 the user olduser is gone; locking keeps the account. Run the setup of this task again with Restart and lock it instead of deleting it"
  rc=1
fi

status=$(passwd -S olduser 2>/dev/null | awk '{print $2}')
if [ "$status" = L ]; then
  echo "PASS 2"
else
  echo "FAIL 2 olduser can still log in with a password; lock the account with passwd -l olduser or usermod -L olduser, then check it with passwd -S olduser"
  rc=1
fi
exit $rc
