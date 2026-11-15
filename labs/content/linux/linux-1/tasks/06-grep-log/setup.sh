#!/bin/sh
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -rf logs errors.txt
mkdir logs
cat > logs/app.log <<LOG
2025-11-14 09:00:01 INFO server started
2025-11-14 09:00:05 INFO user ana logged in
2025-11-14 09:01:12 ERROR cannot open file /data/report.csv
2025-11-14 09:02:40 WARN disk is 80 percent full
2025-11-14 09:03:15 ERROR connection to database lost
2025-11-14 09:04:02 INFO retrying connection
2025-11-14 09:04:09 ERROR retry failed
2025-11-14 09:05:30 INFO user dan logged in
2025-11-14 09:06:11 ERROR timeout while saving profile
2025-11-14 09:07:00 INFO server stopped
LOG
' </dev/null
exit $?
