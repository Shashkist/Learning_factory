#!/bin/sh
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -rf inbox backup pictures
mkdir inbox backup pictures
echo "Quarterly report draft" > inbox/report.txt
echo "Photo of the team" > inbox/photo.txt
' </dev/null
exit $?
