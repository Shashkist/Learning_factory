#!/bin/sh
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -rf inbox backup pictures
mkdir inbox backup pictures
echo "Quarterly report draft" > inbox/report.txt
echo "Photo of the team" > inbox/photo.txt
' </dev/null
exit $?
