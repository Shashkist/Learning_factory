#!/bin/sh
# Task 03: the student has not written ~/nameserver.txt yet.
# Runs as root; safe to run again. The home folder belongs to student, so student removes the file.
su student -s /bin/sh -c 'rm -f /home/student/nameserver.txt' || exit 1
exit 0
