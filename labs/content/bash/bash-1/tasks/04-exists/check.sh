#!/bin/sh
# Task 04: ~/exists.sh prints found or missing for a path.
# The student's script runs only as student, on a file, a folder and a path made by the reader in
# a private temporary folder (nothing is left behind), with no stdin and a 3 s limit. The student
# side prints facts, root decides (D52, D191).
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
trap 'rm -rf "$d"' EXIT
S=/home/student/exists.sh
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
  mkdir "$d/archive"
  : > "$d/report.txt"
  run a "$S" "$d/report.txt"
  run b "$S" "$d/archive"
  run c "$S" "$d/nothing.txt"
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
    else fail 1 "the first line of ~/exists.sh must be #!/bin/bash, so the system knows which shell runs the script; add it at the top with nano ~/exists.sh"; fi;;
  noexec) fail 1 "~/exists.sh is not executable; make it executable with chmod +x ~/exists.sh";;
  *) fail 1 "the file ~/exists.sh does not exist; create it with nano ~/exists.sh";;
esac
if [ "$st" != ok ]; then
  fail 2 "~/exists.sh cannot run yet; fix the first item"
  fail 3 "~/exists.sh cannot run yet; fix the first item"
  fail 4 "~/exists.sh cannot run yet; fix the first item"
  exit 1
fi
x a; ra=$r; oa=$o
x b; rb=$r; ob=$o
x c; rc3=$r; oc=$o
if late "$ra" || late "$rb" || late "$rc3"; then
  fail 2 "~/exists.sh ran for more than 3 seconds; it should print one word and stop, not wait for input"
  fail 3 "~/exists.sh ran for more than 3 seconds; it should print one word and stop, not wait for input"
  fail 4 "~/exists.sh did not finish in time, so it has no exit code"
  exit 1
fi
if [ "$oa" = "found|" ] && [ "$ob" = "found|" ]; then
  pass 2
elif [ "$oa" = "found|" ]; then
  fail 2 "~/exists.sh did not say found for an existing folder; -e is true for files and folders, -f only for files"
elif [ "$ob" = "found|" ]; then
  fail 2 "~/exists.sh did not say found for an existing file; -e is true for files and folders, -d only for folders"
else
  fail 2 "~/exists.sh must print found, in small letters and alone on the line, for a path that exists"
fi
if [ "$oc" = "missing|" ]; then
  pass 3
else
  fail 3 "~/exists.sh must print missing, in small letters and alone on the line, for a path that does not exist"
fi
if [ "$ra" = 0 ] && [ "$rb" = 0 ] && [ "$rc3" = 0 ]; then
  pass 4
else
  fail 4 "~/exists.sh should exit with code 0 both for found and for missing; look at echo \$? after each run"
fi
exit $rc
