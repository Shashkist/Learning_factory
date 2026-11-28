#!/bin/sh
# Task 04-temp: ~/temp.py prints a Celsius temperature in Fahrenheit.
# The student's script runs only as student (never as root), through python3, on several values
# (some are the examples in the task text, others are not), with no stdin and a 4 s limit per run;
# the first run that hangs ends the runs, and after 9 s no new run starts (the manager stops a
# check at 20 s, so a slow but correct script gets a hint, not a timeout). Nothing is written in the student's home. The student side prints facts, root decides
# (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/temp.py
dead=0
t0=$(date +%s)
run() {
  k=$1; shift
  if [ "$dead" = 1 ]; then printf 'X %s 124 \n' "$k"; return; fi
  if [ $(( $(date +%s) - t0 )) -ge 9 ]; then printf 'X %s T \n' "$k"; return; fi
  timeout -k 1 4 python3 -B "$S" "$@" </dev/null >"$d/o" 2>/dev/null
  rc=$?
  case $rc in 124|137) dead=1;; esac
  printf 'X %s %s %s\n' "$k" "$rc" "$(head -c 1000 "$d/o" | tr '\n\000' '|?')"
}
if [ -f "$S" ]; then echo "S ok"; else echo "S missing"; fi
if [ -f "$S" ]; then
  run 0 0
  run 37 37
  run -40 -40
  run 25 25
  run 36.6 36.6
  run 21.5 21.5
  run 18.3 18.3
fi
READER
)
rc=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s %s\n' "$1" "$2"; rc=1; }
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
    if [ "$r" = T ]; then bad=$(slow "python3 ~/temp.py $1"); tip="make it finish quickly"; break; fi
    if [ "$o" != "$2|" ]; then bad="python3 ~/temp.py $1 should print $2, it printed $(shown)"; break; fi
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
    if [ "$r" = T ]; then bad=$(slow "python3 ~/temp.py $a"); tip="make it finish quickly"; break; fi
    if [ "$r" != 0 ]; then bad="python3 ~/temp.py $a did not finish cleanly (exit code $r)"; break; fi
  done
  if [ -n "$bad" ]; then fail "$item" "$bad; $tip"; else pass "$item"; fi
}

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
if [ "$st" = ok ]; then
  pass 1
else
  fail 1 "the file ~/temp.py does not exist; create it with nano ~/temp.py"
  fail 2 "~/temp.py cannot run yet; fix the first item"
  fail 3 "~/temp.py cannot run yet; fix the first item"
  fail 4 "~/temp.py cannot run yet; fix the first item"
  exit 1
fi
if printf '%s\n' "$facts" | grep -qE '^X [^ ]+ (124|137) '; then
  fail 2 "~/temp.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 3 "~/temp.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 4 "~/temp.py did not finish in time, so it has no exit code"
  exit 1
fi
grp 2 "F = C * 9 / 5 + 32, rounded to one decimal place and always shown with one decimal (32.0, not 32)" "0" "32.0" "37" "98.6" "-40" "-40.0" "25" "77.0"
grp 3 "the Celsius value may have a decimal point, so turn the argument into a number that allows it" "36.6" "97.9" "21.5" "70.7" "18.3" "64.9"
codes 4 "print the number and let the script end normally" "0" "37" "-40" "25" "36.6" "21.5" "18.3"
exit $rc
