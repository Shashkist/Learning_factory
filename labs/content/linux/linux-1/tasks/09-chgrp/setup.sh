#!/bin/sh
# The group `team` (with student as a member) comes from the image.
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -f team-notes.txt
echo "meeting at ten" > team-notes.txt
' </dev/null
exit $?
