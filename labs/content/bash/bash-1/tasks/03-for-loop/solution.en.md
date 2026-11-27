# Solution

```bash
mkdir -p ~/files
for i in 1 2 3 4 5; do
  touch ~/files/file$i
done
ls ~/files
```

The loop runs five times, and each time `$i` holds the next number, so `touch` makes `file1`, then `file2`, up to `file5`. `mkdir -p` does not complain when the folder is already there. `seq 1 5` or `{1..5}` give the same numbers.
