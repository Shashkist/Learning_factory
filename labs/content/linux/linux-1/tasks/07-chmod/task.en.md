# Change permissions

Two files need your attention. Run `ls -l ~/scripts ~/secret.txt` to see their permissions.

1. `~/scripts/hello.sh` is a small script, but you cannot run it. Give yourself (the owner) permission to execute it.
2. `~/secret.txt` is readable by everyone. Make it private: only you may read and write it, nobody else gets any permission.

Hint: `chmod +x file` adds the execute permission. You can also give permissions as numbers, where 4 is read, 2 is write and 1 is execute, one digit each for owner, group and others.
