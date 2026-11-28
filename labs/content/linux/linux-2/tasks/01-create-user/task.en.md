# Create a user

A new developer joins the team. Create an account for them.

1. The user is called `dev1`.
2. It has a home folder, `/home/dev1`.
3. Its login shell is `/bin/bash`.

Look at the result with `getent passwd dev1`: the last two fields are the home folder and the shell.

Hint: `useradd` has an option for the home folder and an option for the shell. Read `man useradd`. Your account has `sudo`, so put it in front of commands that need root.
