#!/bin/sh
# Task 05: no sleep 600 of the student is running and ~/sleep.pid does not exist.
# Runs as root; the kill goes through su because root has no CAP_KILL for the student's processes.
su student -s /bin/sh -c '
pkill -f "^sleep 600( |\$)" >/dev/null 2>&1
rm -f /home/student/sleep.pid
exit 0
' </dev/null
exit 0
