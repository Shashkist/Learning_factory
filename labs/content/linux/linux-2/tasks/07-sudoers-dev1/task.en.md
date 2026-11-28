# Give one command with sudo

`dev1` must be able to look at the state of services, and nothing more.

1. Create the file `/etc/sudoers.d/dev1`.
2. It lets `dev1` run only `/usr/bin/systemctl status *` as root, through `sudo`.
3. The file passes `visudo -cf /etc/sudoers.d/dev1`.
4. The file has mode `0440`.

A mistake in a sudoers file can lock everybody out of `sudo`, which is why `visudo` checks it first.

Hint: a rule looks like `user host=(runas) command`, for example `alice ALL=(root) /usr/bin/less /var/log/*`. Write the file with `sudo tee` or `sudo visudo -f /etc/sudoers.d/dev1`, then fix the mode with `chmod`.
