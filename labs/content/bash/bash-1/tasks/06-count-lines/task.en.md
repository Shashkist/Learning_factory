# Count the lines of a file with while read

Write a script `~/count_lines.sh` that counts the lines of the file you give it. Use a `while read` loop.

1. `~/count_lines.sh FILE` prints the number of lines in FILE, and only the number.
2. A last line that has no newline at the end still counts as a line.
3. An empty file gives `0`, and blank lines count too.
4. Make the script executable.

To try it, setup made `~/data/fruits.txt`. Its last line has no newline at the end, so the right answer for it is `5`.

Hint: `while read -r line; do ...; done < "$1"` runs the body once for every line of the file. `n=$((n + 1))` adds one to a variable. Remember that `read` gives up on a last line that has no newline.
