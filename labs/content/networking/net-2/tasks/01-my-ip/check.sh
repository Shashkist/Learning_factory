#!/bin/sh
# Task 01: ~/myip.txt holds this machine's IPv4 address with its prefix, as `ip addr` prints it.
# State only. The true value is computed here, from the machine, every time it runs.
F=/home/student/myip.txt
exists=$(su student -s /bin/sh -c "[ -f $F ] && echo y" 2>/dev/null)
content=$(su student -s /bin/sh -c "cat $F" 2>/dev/null)
words=$(printf '%s' "$content" | wc -w)
# the value counts only when the file holds exactly one non-empty line with a single word
val=$(printf '%s\n' "$content" | awk 'NF{n++; w=NF; v=$1} END{if (n==1 && w==1) print v}')
mine=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}')
rc=0

if [ "$exists" = y ] && [ "$words" -ge 1 ]; then
  echo "PASS 1"
else
  echo "FAIL 1 ~/myip.txt is missing or empty; write the address into it, for example with > ~/myip.txt"
  rc=1
fi

if printf '%s\n' "$val" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$'; then
  echo "PASS 2"
else
  echo "FAIL 2 the file must hold one value like a.b.c.d/N, the address and the prefix length, and nothing else; take only the second column of the inet line of ip addr"
  rc=1
fi

if [ -n "$val" ] && printf '%s\n' "$mine" | grep -Fxq -- "$val"; then
  echo "PASS 3"
else
  echo "FAIL 3 that is not the address of this machine; use the inet line of ip addr that is not 127.0.0.1/8 (try ip -4 addr show scope global), with its /prefix"
  rc=1
fi
exit $rc
