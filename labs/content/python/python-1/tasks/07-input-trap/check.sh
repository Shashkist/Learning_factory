#!/bin/sh
# Task 07-input-trap: ~/add.py prints the sum of two whole numbers.
# The student's script runs only as student (never as root), through python3, on several values
# (some are the examples in the task text, others are not), with no stdin and a 4 s limit per run;
# the first run that hangs ends the runs, and after 9 s no new run starts (the manager stops a
# check at 20 s, so a slow but correct script gets a hint, not a timeout). Nothing is written in the student's home. The student side prints facts, root decides
# (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/add.py
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
  run 3_4 3 4
  run 10_25 10 25
  run 100_200 100 200
  run 12_30 12 30
  run -2_9 -2 9
  run 0_0 0 0
  run -5_-6 -5 -6
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
# grp ITEM TRAPTIP ALTTIP ARGS1 WANT1 ARGS2 WANT2 ...: every run must print exactly its answer
grp() {
  item=$1; tip=$2; alt=$3; shift 3
  bad=
  while [ $# -ge 2 ]; do
    x "$(printf '%s' "$1" | tr ' ' _)"
    if [ "$r" = T ]; then bad=$(slow "python3 ~/add.py $1"); tip="make it finish quickly"; break; fi
    if [ "$o" != "$2|" ]; then bad="python3 ~/add.py $1 should print $2, it printed $(shown)"
      case $1 in *' '*) j=${1% *}${1#* }; w=$(shown)
        if [ "$w" = "$j" ]; then :
        elif [ "$w" = nothing ]; then tip="it printed nothing, so it stopped with an error: run python3 ~/add.py $1 yourself and read the error"
        else case $w in *.*) tip=$alt;; *) tip="print the sum of the two numbers: int(sys.argv[1]) + int(sys.argv[2])";; esac
        fi;; esac
      break; fi
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
    if [ "$r" = T ]; then bad=$(slow "python3 ~/add.py $a"); tip="make it finish quickly"; break; fi
    if [ "$r" != 0 ]; then bad="python3 ~/add.py $a did not finish cleanly (exit code $r)"; break; fi
  done
  if [ -n "$bad" ]; then fail "$item" "$bad; $tip"; else pass "$item"; fi
}

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
if [ "$st" = ok ]; then
  pass 1
else
  fail 1 "the file ~/add.py does not exist; create it with nano ~/add.py"
  fail 2 "~/add.py cannot run yet; fix the first item"
  fail 3 "~/add.py cannot run yet; fix the first item"
  fail 4 "~/add.py cannot run yet; fix the first item"
  exit 1
fi
if printf '%s\n' "$facts" | grep -qE '^X [^ ]+ (124|137) '; then
  fail 2 "~/add.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 3 "~/add.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 4 "~/add.py did not finish in time, so it has no exit code"
  exit 1
fi
grp 2 "the arguments arrive as strings, not numbers: adding two strings joins them (3 and 4 give 34), so turn both into numbers first" "print a whole number such as 7, not 7.0: use int(), not float()" "3 4" "7" "10 25" "35" "100 200" "300" "12 30" "42"
grp 3 "zero and negative numbers must work too, and both arguments must be numbers before you add them" "zero and negative numbers must work too, and the answer must be a whole number: use int(), not float()" "-2 9" "7" "0 0" "0" "-5 -6" "-11"
codes 4 "print the sum and let the script end normally" "3 4" "10 25" "100 200" "12 30" "-2 9" "0 0" "-5 -6"
exit $rc
