# Solution

```bash
chmod +x ~/scripts/hello.sh
chmod 600 ~/secret.txt
ls -l ~/scripts ~/secret.txt
```

`+x` adds execute. `600` means: owner 6 (read 4 + write 2), group 0, others 0. Now `ls -l` shows `-rw-------` for the secret.
