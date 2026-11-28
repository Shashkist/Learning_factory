#!/bin/sh
# Task 05: ~/sleep.pid holds the PID of a running sleep 600.
# One su call runs a fixed reader, piped on stdin, that only prints facts: the first line of the
# file, whether that PID is exactly a sleep 600, and the PIDs of every running sleep 600.
# "Exactly" means the command line split on NUL is: argv[0] with the basename sleep, then 600.
# Root decides.
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
is_sleep600() {
  [ "$(tr '\0' '\n' < "$1" 2>/dev/null | awk 'NR==1{a=$0; sub(/.*\//,"",a)} NR==2{b=$0} END{if(NR==2&&a=="sleep"&&b=="600")print "y"}')" = y ]
}
f=/home/student/sleep.pid
pid=
if [ -f "$f" ]; then
  echo "F 1"
  pid=$(head -n 1 "$f" 2>/dev/null | tr -d ' \t\r')
  echo "P $pid"
else
  echo "F 0"
fi
case $pid in
  ''|*[!0-9]*) ;;
  *) if is_sleep600 "/proc/$pid/cmdline"; then echo "C ok"; fi;;
esac
for d in /proc/[0-9]*; do
  if is_sleep600 "$d/cmdline"; then echo "R ${d#/proc/}"; fi
done
READER
)
tag() { printf '%s\n' "$facts" | sed -n "s/^$1 //p" | head -n 1; }
pid=$(tag P)
cmd=$(tag C)
rc=0

case $pid in
  ''|*[!0-9]*)
    if [ "$(tag F)" = 1 ]; then
      echo "FAIL 1 ~/sleep.pid exists but it does not hold only a number; write just the PID, for example with echo \$! > ~/sleep.pid"
    else
      echo "FAIL 1 the file ~/sleep.pid does not exist; write the PID of the sleep into it with >"
    fi
    rc=1;;
  *) echo "PASS 1";;
esac

if printf '%s\n' "$facts" | grep -q '^R '; then
  echo "PASS 2"
else
  echo "FAIL 2 no process sleep 600 is running; start it in the background with sleep 600 & (not another number of seconds)"
  rc=1
fi

case $cmd in
  ok) echo "PASS 3";;
  *)
    echo "FAIL 3 the number in ~/sleep.pid (${pid:-none}) is not the PID of a running sleep 600; take it from \$! right after you start it, or from pgrep -x sleep (\$\$ is your shell, not the sleep)"
    rc=1;;
esac
exit $rc
