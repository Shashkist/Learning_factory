# Create a service user

A service called `app` needs its own account, but nobody should ever log in with it.

1. Create the user `svc-app`.
2. Its shell is `/usr/sbin/nologin`, so it cannot open a terminal.
3. It has no home folder: `/home/svc-app` must not exist.

Look at the result with `getent passwd svc-app` and `ls /home`.

Hint: `useradd` creates a home folder only when you ask for it, and a few of its defaults depend on the system. Read `man useradd` and check the shell and the home folder of the new user.
