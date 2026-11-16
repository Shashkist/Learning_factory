#!/bin/sh
# Task 07: modes of ~/scripts/hello.sh and ~/secret.txt.
# Root has no DAC override here (D45), so a private home would hide the student's files from it.
# One su call runs a fixed reader, piped on stdin, that only prints facts about the paths the task
# looks at. Root parses the facts and decides; no answer, hint or comparison reaches a student
# process, command line or file (D52).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
# f key path: type, mode, owner, group. c key path: readable flag and the lines.
f() {
  if [ -d "$2" ]; then t=d; elif [ -f "$2" ]; then t=f; elif [ -e "$2" ]; then t=o
  else printf '%s\n' "F $1 - - - -"; return; fi
  printf '%s\n' "F $1 $t $(stat -c '%a %U %G' "$2" 2>/dev/null || echo '- - -')"
}
c() {
  if [ -f "$2" ] && ( : < "$2" ) 2>/dev/null; then
    printf '%s\n' "R $1 1"
    head -c 65536 "$2" | head -n 200 | while IFS= read -r l || [ -n "$l" ]; do
      printf '%s\n' "C $1 $l"
    done
  else
    printf '%s\n' "R $1 0"
  fi
}
f hello /home/student/scripts/hello.sh
f secret /home/student/secret.txt
READER
)
# Root side: fact sets ty mo ow gr for a key; rd tells whether the student could read it; body prints its lines.
fact() {
  ty=-; mo=-; ow=-; gr=-
  set -f; set -- $(printf '%s\n' "$facts" | sed -n "s/^F $1 //p" | head -n 1); set +f
  if [ $# -eq 4 ]; then ty=$1; mo=$2; ow=$3; gr=$4; fi
  return 0
}
rd() { printf '%s\n' "$facts" | grep -qx "R $1 1"; }
body() { printf '%s\n' "$facts" | sed -n "s/^C $1 //p"; }
rc=0

# digits of the mode from the facts, e.g. 644 -> u=6 g=4 o=4; fails when there is no mode
digits() {
  case $mo in ''|-|*[!0-9]*) return 1;; esac
  m=$(printf '%04d' "$mo")
  m=${m#?}
  u=${m%??}
  g=${m#?}; g=${g%?}
  o=${m#??}
}

# A shell script runs only when its owner has x and r (the shell reads the file): u is 5 or 7.
fact hello
if [ "$ty" = f ] && digits && [ $((u % 2)) -eq 1 ] && [ "$u" -ge 4 ]; then
  echo "PASS 1"
elif [ "$ty" = f ] && digits && [ $((u % 2)) -eq 1 ]; then
  echo "FAIL 1 the owner has x on ~/scripts/hello.sh but not r; the shell must read a script to run it"
  rc=1
else
  echo "FAIL 1 the owner cannot run ~/scripts/hello.sh yet; the x permission is missing"
  rc=1
fi

fact secret
if [ "$ty" = f ] && digits && [ "$g" -eq 0 ] && [ "$o" -eq 0 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 other people can still access ~/secret.txt; look at its permissions with ls -l"
  rc=1
fi

if [ "$ty" = f ] && digits && [ "$u" -ge 6 ]; then
  echo "PASS 3"
else
  echo "FAIL 3 you locked yourself out of ~/secret.txt; the owner needs r and w"
  rc=1
fi
exit $rc
