#!/bin/sh
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -rf notes backup.tar.gz backup.sh
mkdir -p notes/2025
echo "buy bread and milk" > notes/todo.txt
echo "learn bash scripting" > notes/ideas.txt
echo "meeting on Sunday" > notes/2025/plan.txt
' </dev/null
exit $?
