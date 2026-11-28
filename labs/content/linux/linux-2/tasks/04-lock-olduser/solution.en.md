# Solution

```bash
sudo passwd -l olduser
sudo passwd -S olduser
```

`passwd -l` puts a `!` in front of the password hash, so no password matches any more, and the account and its files stay. `sudo usermod -L olduser` does the same. `passwd -S` now shows `L`. Changing the shell or deleting the user is not the same thing: the first leaves the password usable, the second removes the account.
