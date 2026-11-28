# Solution

```bash
sudo useradd -m -s /bin/bash dev1
getent passwd dev1
```

`-m` creates the home folder (without it `/home/dev1` does not exist) and `-s` sets the login shell (the default is `/bin/sh`). `getent passwd dev1` should end with `:/home/dev1:/bin/bash`.
