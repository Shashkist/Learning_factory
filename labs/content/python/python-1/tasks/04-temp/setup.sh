#!/bin/sh
su student -s /bin/sh -c '
set -e
cd /home/student
rm -f temp.py
' </dev/null
exit $?
