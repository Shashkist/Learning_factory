# Solution

```bash
echo 'dev1 ALL=(root) /usr/bin/systemctl status *' | sudo tee /etc/sudoers.d/dev1
sudo chmod 0440 /etc/sudoers.d/dev1
sudo visudo -cf /etc/sudoers.d/dev1
sudo -l -U dev1
```

The rule says: user `dev1`, on any host, may run as `root` exactly the command `/usr/bin/systemctl status *` (the `*` matches any arguments after `status`). `sudo` ignores files that are writable by others, so the mode must be `0440`. `visudo -cf` only checks the syntax and prints `parsed OK`. `sudo -l -U dev1` lists what `dev1` may run: one line, and nothing like `ALL` or `systemctl *`.
