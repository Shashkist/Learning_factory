#!/bin/sh
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -f notes/todo.txt
mkdir -p notes
' </dev/null
exit $?
