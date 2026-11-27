#!/bin/sh
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -rf data count_lines.sh
mkdir data
printf "apple\nbanana\ncherry\ndate\nelderberry" > data/fruits.txt
' </dev/null
exit $?
