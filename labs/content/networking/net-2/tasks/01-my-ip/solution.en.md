# Solution

```bash
ip addr
ip -4 -o addr show scope global | awk '{print $4}' > ~/myip.txt
cat ~/myip.txt
```

`ip addr` lists the network cards. The `inet` line of the card that is not `lo` looks like `inet 172.20.0.5/16 brd ... scope global eth0`. The address is the second word, `172.20.0.5/16`: `172.20.0.5` is the address and `/16` says that the first 16 bits are the network part. You can also copy the value by hand into the file with `echo`. The loopback `127.0.0.1/8` only reaches the machine itself.
