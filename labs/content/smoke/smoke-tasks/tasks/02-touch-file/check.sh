#!/bin/sh
# PASS 1 when /home/student/smoke-ok exists, FAIL 1 with a hint otherwise.
if [ -e /home/student/smoke-ok ]; then
  echo "PASS 1"
  exit 0
fi
echo "FAIL 1 File ~/smoke-ok does not exist yet"
exit 1
