#!/bin/sh
# Task 02-grade: ~/grade.py prints pass or fail for a grade.
# The student's script runs only as student (never as root), through python3, on values that are
# in no task text, with no stdin and a 4 s limit per run; the first run that hangs ends the
# runs. Nothing is written in the student's home. The student side prints facts, root decides
# (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/grade.py
dead=0
run() {
  k=$1; shift
  if [ "$dead" = 1 ]; then printf 'X %s 124 \n' "$k"; return; fi
  timeout -k 1 4 python3 -B "$S" "$@" </dev/null >"$d/o" 2>/dev/null
  rc=$?
  case $rc in 124|137) dead=1;; esac
  printf 'X %s %s %s\n' "$k" "$rc" "$(head -c 1000 "$d/o" | tr '\n\000' '|?')"
}
if [ -f "$S" ]; then echo "S ok"; else echo "S missing"; fi
if [ -f "$S" ]; then
  run 60 60
  run 99 99
  run 100 100
  run 83 83
  run 0 0
  run 59 59
  run 9 9
  run 45 45
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
# grp ITEM TIP ARGS1 WANT1 ARGS2 WANT2 ...: every run must print exactly its answer
grp() {
  item=$1; tip=$2; shift 2
  bad=
  while [ $# -ge 2 ]; do
    x "$(printf '%s' "$1" | tr ' ' _)"
    if [ "$o" != "$2|" ]; then bad="python3 ~/grade.py $1 should print $2"; break; fi
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
    if [ "$r" != 0 ]; then bad="python3 ~/grade.py $a did not finish cleanly (exit code $r)"; break; fi
  done
  if [ -n "$bad" ]; then fail "$item" "$bad; $tip"; else pass "$item"; fi
}

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
if [ "$st" = ok ]; then
  pass 1
else
  fail 1 "the file ~/grade.py does not exist; create it with nano ~/grade.py"
  fail 2 "~/grade.py cannot run yet; fix the first item"
  fail 3 "~/grade.py cannot run yet; fix the first item"
  fail 4 "~/grade.py cannot run yet; fix the first item"
  exit 1
fi
if printf '%s\n' "$facts" | grep -qE '^X [^ ]+ (124|137) '; then
  fail 2 "~/grade.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 3 "~/grade.py ran for more than 4 seconds; it should print one line and stop, not wait for input"
  fail 4 "~/grade.py did not finish in time, so it has no exit code"
  exit 1
fi
grp 2 "60 itself is a pass, so compare numbers with >= 60 (int(sys.argv[1]) gives a number)" "60" "pass" "99" "pass" "100" "pass" "83" "pass"
grp 3 "below 60 is a fail, and the argument must be compared as a number, not as text" "0" "fail" "59" "fail" "9" "fail" "45" "fail"
codes 4 "print the word and let the script end normally" "60" "99" "100" "83" "0" "59" "9" "45"
exit $rc
