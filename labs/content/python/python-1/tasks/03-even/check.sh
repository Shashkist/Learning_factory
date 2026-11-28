#!/bin/sh
# Task 03-even: ~/even.py prints even or odd for a whole number.
# The student's script runs only as student (never as root), through python3, on several values
# (some are the examples in the task text, others are not), with no stdin and a 4 s limit per run;
# the first run that hangs ends the runs, and after 12 s no new run starts (the manager stops a
# check at 20 s, so a slow but correct script gets a hint, not a timeout). Nothing is written in the student's home. The student side prints facts, root decides
# (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/even.py
dead=0
t0=$(date +%s)
run() {
  k=$1; shift
  if [ "$dead" = 1 ]; then printf 'X %s 124 \n' "$k"; return; fi
  if [ $(( $(date +%s) - t0 )) -ge 12 ]; then printf 'X %s 125 \n' "$k"; return; fi
  timeout -k 1 4 python3 -B "$S" "$@" </dev/null >"$d/o" 2>/dev/null
  rc=$?
  case $rc in 124|137) dead=1;; esac
  printf 'X %s %s %s\n' "$k" "$rc" "$(head -c 1000 "$d/o" | tr '\n\000' '|?')"
}
if [ -f "$S" ]; then echo "S ok"; else echo "S missing"; fi
if [ -f "$S" ]; then
  run 0 0
  run 10 10
  run -8 -8
  run 2468 2468
  run 7 7
  run -3 -3
  run 1001 1001
  run 13 13
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
# shown: what the script printed, as one short safe line
shown() {
  w=$(printf '%s' "$o" | tr -c '[:print:]' '?' | sed 's/|$//; s/|/ \/ /g; s/^ *//; s/ *$//' | cut -c1-40)
  [ -n "$w" ] || w=nothing
  printf '%s' "$w"
}
slow() { printf '%s' "the check ran out of time before it could try $1: the script is too slow, each run should take well under a second"; }
# grp ITEM TIP ARGS1 WANT1 ARGS2 WANT2 ...: every run must print exactly its answer
grp() {
  item=$1; tip=$2; shift 2
  bad=
  while [ $# -ge 2 ]; do
    x "$(printf '%s' "$1" | tr ' ' _)"
    if [ "$r" = 125 ]; then bad=$(slow "python3 ~/even.py $1"); tip="make it finish quickly"; break; fi
    if [ "$o" != "$2|" ]; then bad="python3 ~/even.py $1 should print $2, it printed $(shown)"; break; fi
    shift 2
  done
  if [ -n "$bad" ]; then fail "$item" "$bad; $tip"; else pass "$item"; fi
}
# codes ITEM TIP ARGS...: every run must exit with code 0
codes() {
  item=$1; tip=$2; shift 2
  bad=
  for a in "$@"; do
    x "$(printf '%s' "$a" | tr ' ' _)"
    if [ "$r" = 125 ]; then bad=$(slow "python3 ~/even.py $a"); tip="make it finish quickly"; break; fi
    if [ "$r" != 0 ]; then bad="python3 ~/even.py $a did not finish cleanly (exit code $r)"; break; fi
  done
  if [ -n "$bad" ]; then fail "$item" "$bad; $tip"; else pass "$item"; fi
}

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
if [ "$st" = ok ]; then
  pass 1
else
  fail 1 "the file ~/even.py does not exist; create it with nano ~/even.py"
  fail 2 "~/even.py cannot run yet; fix the first item"
  fail 3 "~/even.py cannot run yet; fix the first item"
  fail 4 "~/even.py cannot run yet; fix the first item"
  exit 1
fi
if printf '%s\n' "$facts" | grep -qE '^X [^ ]+ (124|137) '; then
  fail 2 "~/even.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 3 "~/even.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 4 "~/even.py did not finish in time, so it has no exit code"
  exit 1
fi
grp 2 "an even number leaves remainder 0 when divided by 2 (n % 2 == 0), and 0 and negative numbers count too" "0" "even" "10" "even" "-8" "even" "2468" "even"
grp 3 "an odd number leaves remainder 1 when divided by 2; check that negative numbers like -3 work" "7" "odd" "-3" "odd" "1001" "odd" "13" "odd"
codes 4 "print the word and let the script end normally" "0" "10" "-8" "2468" "7" "-3" "1001" "13"
exit $rc
