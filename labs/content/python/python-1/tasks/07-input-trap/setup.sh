#!/bin/sh
su student -s /bin/sh -c '
set -e
cd /home/student
rm -f add.py
' </dev/null
exit $?
