#!/bin/sh
# Task 02: ~/greet.sh greets the name it is given.
# The student's script runs only as student (never as root), on names that are not in the task
# text, with no stdin and a 3 s limit. One su call runs a fixed reader, piped on stdin, that only
# prints facts (exit code and the output with newlines turned into |); root parses them and
# decides, so no answer or hint reaches a student process (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/greet.sh
dead=0
# run key command...: exit code and flattened stdout of one run; after a timeout nothing else runs
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
  run a "$S" Tamar
  run b "$S" Ben-Zion
fi
READER
)
rc=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 $2"; rc=1; }
# x key: sets r (exit code) and o (flattened output) of a run; r is - when it did not run
x() {
  g=$(printf '%s\n' "$facts" | sed -n "s/^X $1 //p" | head -n 1)
  r=${g%% *}; o=${g#* }
  [ -n "$g" ] || { r=-; o=; }
}
late() { case $1 in 124|137) return 0;; esac; return 1; }

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
case $st in
  ok) pass 1;;
  noexec) fail 1 "~/greet.sh is not executable; make it executable with chmod +x ~/greet.sh";;
  *) fail 1 "the file ~/greet.sh does not exist; create it with nano ~/greet.sh";;
esac
if [ "$st" != ok ]; then
  fail 2 "~/greet.sh cannot run yet; fix the first item"
  fail 3 "~/greet.sh cannot run yet; fix the first item"
  exit 1
fi
x a; ra=$r; oa=$o
x b; rb=$r; ob=$o
if late "$ra" || late "$rb"; then
  fail 2 "~/greet.sh ran for more than 3 seconds; it should print one line and stop, not wait for input"
elif [ "$oa" = "Hello, Tamar|" ] && [ "$ob" = "Hello, Ben-Zion|" ]; then
  pass 2
else
  fail 2 "~/greet.sh must print Hello, then a space and the first argument, on one line, to standard output; try it with a name of your own"
fi
if [ "$ra" = 0 ] && [ "$rb" = 0 ]; then
  pass 3
elif late "$ra" || late "$rb"; then
  fail 3 "~/greet.sh did not finish in time, so it has no exit code"
else
  fail 3 "~/greet.sh should finish with exit code 0; look at echo \$? right after you run it"
fi
exit $rc
