# Does it exist? if and else

Write a script `~/exists.sh` that takes a path and says whether something is there.

1. `~/exists.sh PATH` prints `found` when the path exists. A file and a folder both count.
2. It prints `missing` when the path does not exist.
3. It exits with code `0` in both cases.
4. Make the script executable, and start it with the line `#!/bin/bash`.

Try it on `/etc/hostname`, on `/etc` and on a path that is not there, such as `/no/such/place`.

Hint: `if [ -e ... ]; then ...; else ...; fi` runs the first part when something exists at that path (`-e` means exists). The path you give the script is `$1`.
