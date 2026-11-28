# Solution

```bash
curl -s http://127.0.0.1:9091/ > ~/pong.txt
cat ~/pong.txt
```

You should see one line, `pong:` and a token.

Why `127.0.0.1` and not `localhost`? The name `localhost` can point to two addresses: the IPv4 address `127.0.0.1` and the IPv6 address `::1`. Many systems try `::1` first. This service listens only on the IPv4 address `127.0.0.1`, and another program, a placeholder web page, listens on the same port `9091` on IPv6 `::1`. So `curl http://localhost:9091/` reaches the placeholder and prints another page, while `curl http://127.0.0.1:9091/` reaches the real service.

The lesson: when a service "does not answer" or answers strangely on `localhost`, try `127.0.0.1` and `::1` explicitly, and use `curl -4` or `curl -6` to force one of them. `ss -ltn` shows which addresses a port listens on.
