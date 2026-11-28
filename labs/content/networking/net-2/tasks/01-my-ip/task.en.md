# Find your own IP address

Every machine on a network has an IP address. Your lab machine has one too, and `ip addr` shows it.

1. Run `ip addr` and find the line that starts with `inet` on the network card that is not the loopback (`lo`). The loopback address `127.0.0.1/8` is not the one you need.
2. Write the address of this machine into `~/myip.txt`, exactly as `ip addr` prints it: the four numbers, a slash and the prefix length (the number after the slash).

The file holds one value, and nothing else.

Hint: the value you need is the second word of that `inet` line.
