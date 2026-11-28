#!/bin/sh
# Task 01: the student has not written ~/myip.txt yet.
# Runs as root; safe to run again. The home folder belongs to student, so student removes the file.
su student -s /bin/sh -c 'rm -f /home/student/myip.txt' || exit 1
exit 0
