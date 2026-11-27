# Solution

```bash
sudo groupadd ops
sudo usermod -aG ops dev1
id dev1
```

`-G ops` sets the list of secondary groups to exactly `ops`, so every other group of the user is dropped. That is the trap. `-a` means append: `usermod -aG ops dev1` adds `ops` and keeps the rest. `sudo gpasswd -a dev1 ops` does the same. If you already lost the old group, add it back with `sudo usermod -aG webdev dev1`.
