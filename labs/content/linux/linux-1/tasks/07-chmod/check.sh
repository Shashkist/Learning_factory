#!/bin/sh
# Root cannot bypass file permissions here, so the check only looks at the mode with stat.
h=/home/student
rc=0

# digits of the mode, e.g. 644 -> u=6 g=4 o=4
mode() {
  m=$(stat -c %a "$1" 2>/dev/null) || return 1
  m=$(printf '%04d' "$m")
  m=${m#?}
  u=${m%??}
  g=${m#?}; g=${g%?}
  o=${m#??}
}

if [ -f "$h/scripts/hello.sh" ] && mode "$h/scripts/hello.sh" && [ $((u % 2)) -eq 1 ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the owner cannot run ~/scripts/hello.sh yet; the x permission is missing"
  rc=1
fi

if [ -f "$h/secret.txt" ] && mode "$h/secret.txt" && [ "$g" -eq 0 ] && [ "$o" -eq 0 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 other people can still access ~/secret.txt; look at its permissions with ls -l"
  rc=1
fi

if [ -f "$h/secret.txt" ] && mode "$h/secret.txt" && [ "$u" -ge 6 ]; then
  echo "PASS 3"
else
  echo "FAIL 3 you locked yourself out of ~/secret.txt; the owner needs r and w"
  rc=1
fi

exit $rc
