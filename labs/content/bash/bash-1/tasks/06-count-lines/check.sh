#!/bin/sh
# Task 06: ~/count_lines.sh prints the number of lines of a file.
# The student's script runs only as student, on four files made by the reader in a private
# temporary folder (nothing is left behind), with no stdin and a 3 s limit. The student side
# prints facts, root decides (D52, D191). The exit code is not judged: grep -c exits 1 for an
# empty file but its answer is right.
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/count_lines.sh
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
  printf 'red\ngreen\nblue\nplum\nlime\nteal\nnavy\n' > "$d/seven.txt"
  printf 'one\ntwo\nthree\nfour' > "$d/four-open.txt"
  : > "$d/empty.txt"
  printf '\n\nx y\n\n' > "$d/blanks.txt"
  run a "$S" "$d/seven.txt"
  run b "$S" "$d/four-open.txt"
  run c "$S" "$d/empty.txt"
  run e "$S" "$d/blanks.txt"
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
  noexec) fail 1 "~/count_lines.sh is not executable; make it executable with chmod +x ~/count_lines.sh";;
  *) fail 1 "the file ~/count_lines.sh does not exist; create it with nano ~/count_lines.sh";;
esac
if [ "$st" != ok ]; then
  fail 2 "~/count_lines.sh cannot run yet; fix the first item"
  fail 3 "~/count_lines.sh cannot run yet; fix the first item"
  fail 4 "~/count_lines.sh cannot run yet; fix the first item"
  exit 1
fi
x a; ra=$r; oa=$o
x b; rb=$r; ob=$o
x c; rc3=$r; oc=$o
x e; re=$r; oe=$o
if late "$ra" || late "$rb" || late "$rc3" || late "$re"; then
  fail 2 "~/count_lines.sh ran for more than 3 seconds; with while read, give the loop the file with < \"\$1\""
  fail 3 "~/count_lines.sh ran for more than 3 seconds; with while read, give the loop the file with < \"\$1\""
  fail 4 "~/count_lines.sh ran for more than 3 seconds; with while read, give the loop the file with < \"\$1\""
  exit 1
fi
if [ "$oa" = "7|" ]; then
  pass 2
else
  fail 2 "for a file of 7 lines ~/count_lines.sh must print just 7; no file name, no extra text"
fi
if [ "$ob" = "4|" ]; then
  pass 3
else
  fail 3 "a last line without a newline at the end must be counted too; test it with printf 'a\\nb' > /tmp/t.txt"
fi
if [ "$oc" = "0|" ] && [ "$oe" = "4|" ]; then
  pass 4
else
  fail 4 "an empty file must give 0, and blank lines are lines too (a file with 2 blank lines, a text line and a blank line has 4)"
fi
exit $rc
