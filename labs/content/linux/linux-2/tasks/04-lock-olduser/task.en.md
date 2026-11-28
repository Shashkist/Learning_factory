# Lock an account

`olduser` belongs to a colleague who left the company. Do not delete the account (their files must stay), but nobody may log in with its password any more.

1. The account `olduser` must still exist.
2. Lock it.

Check the state with `sudo passwd -S olduser`: the second field is `P` for a usable password and `L` for a locked one.

Hint: `man passwd` and `man usermod` both have an option that locks an account.
