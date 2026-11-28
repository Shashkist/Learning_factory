# Solution

```bash
echo '127.0.0.1 web.lab' | sudo tee -a /etc/hosts
getent hosts web.lab
curl -s http://web.lab:8081/ > ~/web-reply.txt
```

Without `sudo`, `echo '127.0.0.1 web.lab' >> /etc/hosts` fails with `Permission denied`: `/etc/hosts` belongs to `root`, and `student` may only read it. The redirect `>>` is done by your own shell, so putting `sudo` in front of `echo` does not help. `sudo tee -a /etc/hosts` runs `tee` as root, and `-a` appends instead of overwriting the file. `sudo sh -c "echo '127.0.0.1 web.lab' >> /etc/hosts"` works too.

After that `getent hosts web.lab` shows `127.0.0.1       web.lab`, and `curl` finds the service by name. Save the page with `curl -s`, without `-i`, so the file holds only the page and not the HTTP headers.
