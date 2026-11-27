# A for loop that makes files

Create the folder `~/files` and use a `for` loop to put five files in it, named `file1`, `file2`, `file3`, `file4` and `file5`.

1. The folder `~/files` exists.
2. It holds the files `file1` to `file5`. They can be empty.
3. It holds nothing else: no sixth file, no folders.

Hint: `mkdir ~/files` makes the folder, `touch path` makes an empty file. The loop looks like `for i in 1 2 3 4 5; do ...; done`, and inside it `$i` is the current number. You can write it in one line in the terminal.
