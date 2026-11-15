#!/bin/sh
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -rf scripts secret.txt
mkdir scripts
printf "#!/bin/sh\necho hello\n" > scripts/hello.sh
chmod 644 scripts/hello.sh
echo "my private notes" > secret.txt
chmod 644 secret.txt
' </dev/null
exit $?
