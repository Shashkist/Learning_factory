#!/bin/sh
su student -s /bin/sh -c '
set -e
cd /home/student
rm -f leap.py
' </dev/null
exit $?
