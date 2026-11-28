# Which server answers the DNS questions?

When a program asks "what is the address of this name?", the machine sends the question to a DNS server. The addresses of these servers are in `/etc/resolv.conf`, one `nameserver` line for each.

1. Read `/etc/resolv.conf` with `cat`.
2. Write the address of the first `nameserver` line into `~/nameserver.txt`. Only the address, without the word `nameserver`.

Hint: the file can also hold other lines, such as `search` and `options`. They are not what you need.
