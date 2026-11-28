#!/bin/sh
# Task 06: a runaway-worker and a decoy runaway-worker-keep run as root, one of each.
# Runs as root; safe to run again (it stops the old ones first, so they never pile up).
D=/usr/local/lib/lf-lab
mkdir -p "$D" || exit 1
ln -sf /usr/bin/sleep "$D/runaway-worker" || exit 1
ln -sf /usr/bin/sleep "$D/runaway-worker-keep" || exit 1
pkill -f "^$D/runaway-worker( |\$)" >/dev/null 2>&1
pkill -f "^$D/runaway-worker-keep( |\$)" >/dev/null 2>&1
setsid nohup "$D/runaway-worker" 86400 >/dev/null 2>&1 </dev/null &
setsid nohup "$D/runaway-worker-keep" 86400 >/dev/null 2>&1 </dev/null &
sleep 1
exit 0
