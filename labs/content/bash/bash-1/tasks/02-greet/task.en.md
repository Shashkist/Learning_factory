# Your first script: greet.sh

Write a script `~/greet.sh` that greets the person whose name you give it.

1. Run as `~/greet.sh NAME`, it prints `Hello, NAME` on one line and nothing else. For example, `~/greet.sh Dana` prints `Hello, Dana`.
2. The script finishes with exit code `0`.
3. Make the script executable, so you can run it like a command.

Hint: the first line of a script is `#!/bin/bash`. The first argument you give a script is `$1`. `chmod +x file` makes a file executable. Create the file with `nano ~/greet.sh`.
