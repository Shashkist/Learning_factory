#!/bin/sh
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -rf docs firstline.sh
mkdir docs
printf "Groceries for Friday\nmilk, eggs, bread\nfruit and vegetables\n" > "docs/my notes.txt"
printf "Welcome to the docs folder\nSecond line of the readme\n" > docs/readme.txt
' </dev/null
exit $?
