# Usage message and exit codes

Good scripts tell you how to use them. Write `~/usage.sh`:

1. With no argument it prints the line `Usage: usage.sh NAME` on standard output, and exits with code `1`.
2. With one argument it exits with code `0`.
3. Make the script executable, and start it with the line `#!/bin/bash`.

After you run it, `echo $?` shows the exit code of the script.

Hint: `$#` is the number of arguments. `[ $# -eq 0 ]` is true when there are none, and `exit 1` ends the script with code 1.
