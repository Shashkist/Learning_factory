# A backup script

Write a script `~/backup.sh` that backs up the folder `~/notes`.

1. Running `~/backup.sh` makes the compressed archive `~/backup.tar.gz` of the folder `~/notes`, with everything inside it.
2. The names inside the archive start with `notes/`, not with `/home/student`.
3. Run again, the script replaces the archive with a fresh one, so a file you deleted from `~/notes` is gone from the archive too.
4. Make the script executable.

Look inside an archive with `tar tzf ~/backup.tar.gz`.

Hint: `tar -czf archive.tar.gz folder` makes a compressed archive (`c` create, `z` gzip, `f` file name). `tar -C ~ ...` first moves to your home folder, so the names inside can start with `notes/`.
