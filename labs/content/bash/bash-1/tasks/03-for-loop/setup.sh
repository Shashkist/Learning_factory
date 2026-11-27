#!/bin/sh
su student -s /bin/sh -c '
set -e
cd /home/student
rm -rf files
' </dev/null
exit $?
