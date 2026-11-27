# Start a process and record its PID

1. Start `sleep 600` in the background, so you keep your prompt.
2. Find the PID (process ID) of that `sleep`.
3. Write the PID, and only the PID, into the file `~/sleep.pid`.

Check yourself: `cat ~/sleep.pid` prints a number, and `ps -p "$(cat ~/sleep.pid)"` shows `sleep`.

Hint: end a command with `&` to run it in the background. The shell keeps the PID of the last background command in the variable `$!`. Or find it with `pgrep -x sleep`.
