#!/bin/sh
# Root has no DAC_OVERRIDE here, so everything in the student's home is done as student.
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -rf projects/website
mkdir -p projects
' </dev/null
exit $?
