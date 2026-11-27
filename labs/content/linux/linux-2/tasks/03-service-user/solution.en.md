# Solution

```bash
sudo useradd -r -s /usr/sbin/nologin svc-app
getent passwd svc-app
ls /home
```

`-s /usr/sbin/nologin` sets the shell that refuses logins. `-r` makes a system account, and a system account gets no home folder. `sudo useradd -M -s /usr/sbin/nologin svc-app` works too: `-M` says explicitly "no home folder".
