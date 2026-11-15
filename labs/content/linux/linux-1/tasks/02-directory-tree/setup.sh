#!/bin/sh
# Root has no DAC_OVERRIDE here, so everything in the student's home is done as student.
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -rf projects/website
mkdir -p projects
' </dev/null
exit $?
