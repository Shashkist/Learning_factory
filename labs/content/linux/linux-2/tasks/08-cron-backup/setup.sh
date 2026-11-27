#!/bin/sh
# Task 08: ~/backup.sh exists without the x bit, and the crontab of student has one old line.
# Runs as root; safe to run again. The script only appends a line to a log in the home folder.
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -f backup.sh
printf "#!/bin/sh\ndate >> \"\$HOME/backup.log\"\n" > backup.sh
chmod 644 backup.sh
printf "# existing job of this machine\n0 3 * * * /usr/bin/true\n" | crontab - || exit 1
exit 0
' </dev/null
exit $?
