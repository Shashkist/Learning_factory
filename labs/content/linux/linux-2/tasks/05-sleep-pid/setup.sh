#!/bin/sh
# Task 05: no sleep 600 is running (the student's or root's) and ~/sleep.pid does not exist.
# Runs as root. The kill runs twice, as root and through su, because without CAP_KILL a process
# can only signal processes of its own user. The match is the one of check.sh: argv[0] has the
# basename sleep and the only argument is 600.
KILLER=$(cat <<'EOF'
for d in /proc/[0-9]*; do
  m=$(tr "\0" "\n" < "$d/cmdline" 2>/dev/null | awk 'NR==1{a=$0; sub(/.*\//,"",a)} NR==2{b=$0} END{if(NR==2&&a=="sleep"&&b=="600")print "y"}')
  [ "$m" = y ] && kill "${d#/proc/}" >/dev/null 2>&1
done
exit 0
EOF
)
sh -c "$KILLER" </dev/null
su student -s /bin/sh -c "$KILLER" </dev/null
rm -f /home/student/sleep.pid
exit 0
