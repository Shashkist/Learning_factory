# Solution

```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"lab":"net-1","msg":"hello"}' http://127.0.0.1:9090/echo > ~/reply.json
cat ~/reply.json
```

`-X POST` chooses the method, `-H` adds the header that tells the service the body is JSON, and `-d` is the body. The reply looks like `{"id": "...", "echo": {"lab": "net-1", "msg": "hello"}}`: the `echo` part is your body and the `id` belongs to the running service.

Without the `Content-Type: application/json` header the service answers `415` with an `error` message, because it does not know how to read the body.
