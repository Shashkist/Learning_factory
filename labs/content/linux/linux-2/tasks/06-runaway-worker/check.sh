#!/bin/sh
# Task 06: runaway-worker is gone and the decoy runaway-worker-keep still runs. State only (/proc).
# A process is found by the name it was started with (first word of its command line), so a
# stopped or half-dead process with an empty command line does not count as running.
main=0
keep=0
for d in /proc/[0-9]*; do
  first=$({ tr '\0' '\n' < "$d/cmdline"; } 2>/dev/null | head -n 1)
  case ${first##*/} in
    runaway-worker) main=$((main + 1));;
    runaway-worker-keep) keep=$((keep + 1));;
  esac
done
rc=0

if [ "$main" -eq 0 ]; then
  echo "PASS 1"
else
  echo "FAIL 1 runaway-worker is still running; find its PID with pgrep -a runaway and stop it with sudo kill PID"
  rc=1
fi

if [ "$keep" -ge 1 ]; then
  echo "PASS 2"
else
  echo "FAIL 2 runaway-worker-keep is not running any more; you stopped it too. Press Restart to get both back, then stop only runaway-worker, by its PID or with pkill -x"
  rc=1
fi
exit $rc
