# First line of any file

Write a script `~/firstline.sh` that prints the first line of the file you give it.

1. `~/firstline.sh FILE` prints the first line of FILE and nothing else.
2. It must work on every file, also on `~/docs/my notes.txt`. Setup made that file, and yes, the name has a space in it.
3. Make the script executable.

Try it on `~/docs/readme.txt` and on `~/docs/my notes.txt`.

Hint: `head -n 1 file` prints the first line of a file. The file you give the script is `$1`.
