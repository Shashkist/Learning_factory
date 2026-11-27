#!/bin/sh
# Task 08: ~/firstline.sh prints the first line of a file, also when the name has a space.
# The student's script runs only as student, on two files made by the reader in a private
# temporary folder (nothing is left behind, the files of the task are not touched), with no
# stdin and a 3 s limit. The student side prints facts, root decides (D52, D191). The exit code
# is not judged, as in task 06: the task never asks for one.
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/firstline.sh
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
if [ -f "$S" ] && [ "$(head -c 2 "$S")" = '#!' ]; then echo "H ok"; else echo "H no"; fi
if [ -f "$S" ] && [ -x "$S" ]; then
  printf 'plain first line\nsecond\nthird\n' > "$d/plain.txt"
  printf 'Quarterly plan draft\nsecond line\nthird line\n' > "$d/plan for may.txt"
  run a "$S" "$d/plain.txt"
  run b "$S" "$d/plan for may.txt"
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
hs=$(printf '%s\n' "$facts" | sed -n 's/^H //p' | head -n 1)
case $st in
  ok)
    if [ "$hs" = ok ]; then pass 1
    else fail 1 "the first line of ~/firstline.sh must be #!/bin/bash, so the system knows which shell runs the script; add it at the top with nano ~/firstline.sh"; fi;;
  noexec) fail 1 "~/firstline.sh is not executable; make it executable with chmod +x ~/firstline.sh";;
  *) fail 1 "the file ~/firstline.sh does not exist; create it with nano ~/firstline.sh";;
esac
if [ "$st" != ok ]; then
  fail 2 "~/firstline.sh cannot run yet; fix the first item"
  fail 3 "~/firstline.sh cannot run yet; fix the first item"
  exit 1
fi
x a; ra=$r; oa=$o
x b; rb=$r; ob=$o
if late "$ra" || late "$rb"; then
  fail 2 "~/firstline.sh ran for more than 3 seconds; it should print one line and stop, not wait for input"
  fail 3 "~/firstline.sh ran for more than 3 seconds; it should print one line and stop, not wait for input"
  exit 1
fi
if [ "$oa" = "plain first line|" ]; then
  pass 2
else
  fail 2 "~/firstline.sh must print the first line of the file it is given, and nothing else; the file is the first argument"
fi
if [ "$ob" = "Quarterly plan draft|" ]; then
  pass 3
else
  fail 3 "it fails when the file name has a space in it: an unquoted variable is split at the spaces into separate words; put quotes around it"
fi
exit $rc
