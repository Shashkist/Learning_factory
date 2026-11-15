#!/bin/sh
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -f notes/todo.txt
mkdir -p notes
' </dev/null
exit $?
