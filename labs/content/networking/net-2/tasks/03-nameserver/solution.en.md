# Solution

```bash
cat /etc/resolv.conf
awk '$1 == "nameserver" {print $2; exit}' /etc/resolv.conf > ~/nameserver.txt
cat ~/nameserver.txt
```

A line like `nameserver 192.0.2.53` names a DNS server: the address is the second word. The machine asks the servers in the order of the lines, so the first one is the one that answers first. The `awk` command prints the second word of the first `nameserver` line and stops. Typing the address by hand with `echo` is fine too.
