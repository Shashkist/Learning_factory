#!/bin/sh
# Task 08: ~/backup.sh is executable and the crontab of student runs it every 5 minutes.
# One su call runs a fixed reader, piped on stdin, that prints the state of the script and the
# crontab lines. Root parses the lines and decides; nothing is run.
facts=$(su student -s /bin/sh -c '/bin/sh -s' <<'READER'
S=/home/student/backup.sh
if [ -f "$S" ] && [ -x "$S" ]; then echo "S ok"
elif [ -f "$S" ]; then echo "S noexec"
else echo "S missing"; fi
crontab -l 2>/dev/null | head -n 200 | sed 's/^/L /'
READER
)
rc=0
case $(printf '%s\n' "$facts" | sed -n 's/^S //p' | head -n 1) in
  ok) echo "PASS 1";;
  noexec) echo "FAIL 1 ~/backup.sh is not executable, so cron cannot run it; make it executable with chmod +x ~/backup.sh"; rc=1;;
  *) echo "FAIL 1 the file ~/backup.sh is missing; run the setup of this task again with Restart"; rc=1;;
esac

# Lines of the crontab that start the script: how many, and how many of them every 5 minutes.
found=0
every5=0
tmp=$(mktemp) || exit 1
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$facts" | sed -n 's/^L //p' > "$tmp"
while IFS= read -r line; do
  set -f
  set -- $line
  set +f
  [ $# -ge 6 ] || continue
  case $1 in '#'*) continue;; esac
  cmd=$6
  case $cmd in bash|sh|/bin/sh|/bin/bash|/usr/bin/bash) [ $# -ge 7 ] && cmd=$7;; esac
  case $cmd in
    '~/backup.sh'|/home/student/backup.sh|'$HOME/backup.sh'|'${HOME}/backup.sh') ;;
    *) continue;;
  esac
  found=$((found + 1))
  if [ "$2 $3 $4 $5" = '* * * *' ]; then
    case $1 in
      '*/5'|0-59/5|00-59/5|0-5[5-9]/5|00-5[5-9]/5|0,5,10,15,20,25,30,35,40,45,50,55) every5=$((every5 + 1));;
    esac
  fi
done < "$tmp"

if [ "$found" -ge 1 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 the crontab of student has no line that runs ~/backup.sh; add one with crontab -e and look at it with crontab -l"
  rc=1
fi

if [ "$every5" -ge 1 ]; then
  echo "PASS 3"
elif [ "$found" -ge 1 ]; then
  echo "FAIL 3 the line that runs ~/backup.sh does not run every 5 minutes; the minute field should be */5 and the other four fields *"
  rc=1
else
  echo "FAIL 3 there is no line for ~/backup.sh to check the schedule of; add it first"
  rc=1
fi
exit $rc
