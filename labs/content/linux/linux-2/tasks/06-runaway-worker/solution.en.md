# Solution

```bash
pgrep -a runaway
sudo kill PID_OF_RUNAWAY_WORKER
pgrep -a runaway
```

Take the PID of the line that says `runaway-worker` (not `runaway-worker-keep`) from `pgrep -a runaway`. By name, use `sudo pkill -x runaway-worker`: `-x` matches the whole process name. Plain `pkill runaway-worker` or `pkill -f runaway-worker` matches both processes, because the name of the second one starts with the name of the first, and the decoy dies too.
