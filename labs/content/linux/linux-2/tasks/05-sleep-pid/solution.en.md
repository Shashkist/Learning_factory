# Solution

```bash
sleep 600 &
echo $! > ~/sleep.pid
cat ~/sleep.pid
```

`&` starts the command in the background and `$!` holds its PID. `pgrep -x sleep > ~/sleep.pid` also works when only one `sleep` is running. Do not use `$$`: it is the PID of your shell, not of the `sleep`. If you close the terminal, the `sleep` may stop with it, so keep the terminal open, or start it with `nohup sleep 600 &`.
