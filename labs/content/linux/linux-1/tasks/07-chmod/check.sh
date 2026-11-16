#!/bin/sh
# Runs as student in one su call: root has no DAC override here (D45), so stat on files in a
# private home (chmod 700 ~) would fail as root.
body=$(cat <<'EOF'
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

# A shell script runs only when its owner has x and r (the shell reads the file): u is 5 or 7.
if [ -f "$h/scripts/hello.sh" ] && mode "$h/scripts/hello.sh" && [ $((u % 2)) -eq 1 ] && [ "$u" -ge 4 ]; then
  echo "PASS 1"
elif [ -f "$h/scripts/hello.sh" ] && mode "$h/scripts/hello.sh" && [ $((u % 2)) -eq 1 ]; then
  echo "FAIL 1 the owner has x on ~/scripts/hello.sh but not r; the shell must read a script to run it"
  rc=1
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
EOF
)
if [ "$(id -u)" -eq 0 ]; then
  exec su student -s /bin/sh -c "$body" </dev/null
fi
exec /bin/sh -c "$body"
