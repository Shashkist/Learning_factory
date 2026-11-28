#!/bin/sh
# Task 03: ~/nameserver.txt holds the address of the FIRST nameserver line of /etc/resolv.conf.
# State only. /etc/resolv.conf is read every time the check runs, so the answer follows the file.
F=/home/student/nameserver.txt
exists=$(su student -s /bin/sh -c "[ -f $F ] && echo y" 2>/dev/null)
content=$(su student -s /bin/sh -c "cat $F" 2>/dev/null)
words=$(printf '%s' "$content" | wc -w)
val=$(printf '%s' "$content" | awk 'NF==1{print $1}')
ns=$(awk '$1 == "nameserver" {print $2; exit}' /etc/resolv.conf 2>/dev/null)
rc=0

if [ "$exists" = y ] && [ "$words" -ge 1 ]; then
  echo "PASS 1"
else
  echo "FAIL 1 ~/nameserver.txt is missing or empty; write the address into it, for example with > ~/nameserver.txt"
  rc=1
fi

if [ "$words" -eq 1 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 the file must hold the address alone, one word: not the whole line with the word nameserver, and not several lines"
  rc=1
fi

if [ -n "$ns" ] && [ "$val" = "$ns" ]; then
  echo "PASS 3"
else
  echo "FAIL 3 that is not the address after the first nameserver line of /etc/resolv.conf; read the file with cat /etc/resolv.conf"
  rc=1
fi
exit $rc
