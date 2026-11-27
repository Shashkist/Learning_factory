#!/bin/sh
su student -s /bin/sh -c '
set -e
cd /home/student
rm -f greet.sh
' </dev/null
exit $?
