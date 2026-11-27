#!/bin/sh
# Task 07: ~/backup.sh makes ~/backup.tar.gz of ~/notes.
# The student's script runs only as student, with no stdin and a 3 s limit. The reader deletes
# the archive, adds a new file to ~/notes, runs the script and lists the archive; then it removes
# the new file, runs the script again and lists it again. That catches a fixed file list, an
# archive made once by hand and a script that appends instead of replacing. The student side
# prints facts, root decides (D52, D191). It leaves ~/notes as it was, and the archive as the
# student's script made it on the last run.
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
cd /home/student || exit 0
d=$(mktemp -d) || exit 0
extra="notes/zz-check-$$.txt"
trap 'rm -f "/home/student/$extra"; rm -rf "$d"' EXIT
S=/home/student/backup.sh
A=/home/student/backup.tar.gz
dead=0
run() {
  k=$1; shift
  if [ "$dead" = 1 ]; then printf 'X %s 124 \n' "$k"; return; fi
  timeout -k 1 3 "$@" </dev/null >"$d/o" 2>/dev/null
  rc=$?
  case $rc in 124|137) dead=1;; esac
  printf 'X %s %s %s\n' "$k" "$rc" "$(head -c 1000 "$d/o" | tr '\n\000' '|?')"
}
# list tag: whether the archive is a gzip tar, and its first 300 names
list() {
  if [ -f "$A" ] && tar tzf "$A" >"$d/l" 2>/dev/null </dev/null; then
    echo "T $1 1"
    head -n 300 "$d/l" | sed "s/^/E $1 /"
  else
    echo "T $1 0"
  fi
}
if [ -f "$S" ] && [ -x "$S" ]; then echo "S ok"
elif [ -f "$S" ]; then echo "S noexec"
else echo "S missing"; fi
if [ -f "$S" ] && [ -x "$S" ]; then
  if [ -d notes ]; then
    rm -f "$A"
    echo "new file" > "$extra"
    echo "K $extra"
    find notes -type f | sed 's/^/N 1 /'
    run a "$S"
    list 1
    rm -f "$extra"
    find notes -type f | sed 's/^/N 2 /'
    run b "$S"
    list 2
  else
    echo "M 1"
  fi
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
tag() { printf '%s\n' "$facts" | sed -n "s/^$1 //p"; }

st=$(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1)
case $st in
  ok) pass 1;;
  noexec) fail 1 "~/backup.sh is not executable; make it executable with chmod +x ~/backup.sh";;
  *) fail 1 "the file ~/backup.sh does not exist; create it with nano ~/backup.sh";;
esac
if [ "$st" != ok ]; then
  for n in 2 3 4 5; do fail $n "~/backup.sh cannot run yet; fix the first item"; done
  exit 1
fi
if printf '%s\n' "$facts" | grep -qx 'M 1'; then
  for n in 2 3 4 5; do fail $n "the folder ~/notes is missing; run the setup of this task again with Restart"; done
  exit 1
fi
x a; ra=$r
x b; rb=$r
if late "$ra" || late "$rb"; then
  for n in 2 3 4 5; do fail $n "~/backup.sh ran for more than 3 seconds; tar should finish at once and nothing should wait for input"; done
  exit 1
fi
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
extra=$(tag K | head -n 1)
tag 'N 1' > "$tmp/n1"
tag 'N 2' > "$tmp/n2"
tag 'E 1' | sed 's|/$||' > "$tmp/e1"
tag 'E 2' | sed 's|/$||' > "$tmp/e2"

if [ "$(tag 'T 1')" = 1 ]; then
  pass 2
else
  fail 2 "after ~/backup.sh there is no ~/backup.tar.gz that tar can read as a compressed archive; use tar -czf ~/backup.tar.gz ..."
fi
if [ "$(tag 'T 1')" = 1 ] && [ -s "$tmp/e1" ] && [ "$(grep -cvE '^notes(/|$)' "$tmp/e1")" -eq 0 ]; then
  pass 3
else
  fail 3 "the names inside the archive must start with notes/; look at them with tar tzf ~/backup.tar.gz, and use tar -C ~ to cut the path"
fi
if [ "$(tag 'T 1')" = 1 ] && [ -s "$tmp/n1" ] && [ "$(grep -cvxF -f "$tmp/e1" "$tmp/n1")" -eq 0 ]; then
  pass 4
else
  fail 4 "the archive does not hold every file of ~/notes, including a file that was just added; the script must archive the whole folder each time"
fi
if [ "$(tag 'T 2')" = 1 ] && [ -s "$tmp/n2" ] && [ "$(grep -cvxF -f "$tmp/e2" "$tmp/n2")" -eq 0 ] \
  && ! grep -qxF "$extra" "$tmp/e2"; then
  pass 5
else
  fail 5 "after a file was deleted from ~/notes, running ~/backup.sh again must give a fresh archive without it"
fi
exit $rc
