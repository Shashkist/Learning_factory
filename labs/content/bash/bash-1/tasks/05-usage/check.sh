#!/bin/sh
# Task 05: ~/usage.sh prints a usage line and exits 1 with no argument, exits 0 with one.
# The student's script runs only as student, with no stdin and a 3 s limit. The student side
# prints facts (exit code, flattened stdout; stderr is dropped), root decides (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/usage.sh
dead=0
run() {
  k=$1; shift
  if [ "$dead" = 1 ]; then printf 'X %s 124 \n' "$k"; return; fi
  timeout -k 1 3 "$@" </dev/null >"$d/o" 2>/dev/null
  rc=$?
  case $rc in 124|137) dead=1;; esac
  printf 'X %s %s %s\n' "$k" "$rc" "$(head -c 1000 "$d/o" | tr '\n\000' '|?')"
}
if [ -f "$S" ] && [ -x "$S" ]; then echo "S ok"
elif [ -f "$S" ]; then echo "S noexec"
else echo "S missing"; fi
if [ -f "$S" ] && [ -x "$S" ]; then
  run a "$S"
  run b "$S" Noa
fi
READER
)
rc=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
x() {
  g=$(printf '%s\n' "$facts" | sed -n "s/^X $1 //p" | head -n 1)
  r=${g%% *}; o=${g#* }
  [ -n "$g" ] || { r=-; o=; }
}
late() { case $1 in 124|137) return 0;; esac; return 1; }

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
case $st in
  ok) pass 1;;
  noexec) fail 1 "~/usage.sh is not executable; make it executable with chmod +x ~/usage.sh";;
  *) fail 1 "the file ~/usage.sh does not exist; create it with nano ~/usage.sh";;
esac
if [ "$st" != ok ]; then
  fail 2 "~/usage.sh cannot run yet; fix the first item"
  fail 3 "~/usage.sh cannot run yet; fix the first item"
  fail 4 "~/usage.sh cannot run yet; fix the first item"
  exit 1
fi
x a; ra=$r; oa=$o
x b; rb=$r; ob=$o
if late "$ra" || late "$rb"; then
  fail 2 "~/usage.sh ran for more than 3 seconds; it should print one line and stop, not wait for input"
  fail 3 "~/usage.sh did not finish in time, so it has no exit code"
  fail 4 "~/usage.sh did not finish in time, so it has no exit code"
  exit 1
fi
if [ "$oa" = "Usage: usage.sh NAME|" ]; then
  pass 2
else
  fail 2 "with no argument ~/usage.sh must print exactly the line Usage: usage.sh NAME on standard output; test it with [ \$# -eq 0 ]"
fi
if [ "$ra" = 1 ]; then
  pass 3
else
  fail 3 "with no argument ~/usage.sh should exit with code 1 (exit 1); run it and look at echo \$?"
fi
if [ "$rb" = 0 ]; then
  pass 4
else
  fail 4 "with one argument ~/usage.sh should exit with code 0, but it exited with $rb"
fi
exit $rc
