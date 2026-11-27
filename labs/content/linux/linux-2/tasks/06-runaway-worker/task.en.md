# Stop a runaway process

A process called `runaway-worker` is running and burns the machine for nothing. Next to it runs a healthy `runaway-worker-keep`, which the team still needs.

1. Find `runaway-worker` and stop it.
2. Leave `runaway-worker-keep` running.

Both processes belong to `root`, so you need `sudo`. Look at them with `ps aux | grep runaway` or `pgrep -a runaway`, and check again after you stop one.

Hint: you can stop a process by its PID (`kill`) or by its name (`pkill`). Careful: a name is matched as a pattern, and one name contains the other.
