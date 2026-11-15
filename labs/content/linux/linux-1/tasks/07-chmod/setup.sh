#!/bin/sh
su student -s /bin/sh -c '
set -e
umask 022
cd /home/student
rm -rf scripts secret.txt
mkdir scripts
printf "#!/bin/sh\necho hello\n" > scripts/hello.sh
chmod 644 scripts/hello.sh
echo "my private notes" > secret.txt
chmod 644 secret.txt
' </dev/null
exit $?
