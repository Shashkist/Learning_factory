# Send a POST request

Until now you only asked for pages (GET). A POST request sends data to the server. A small echo service is running on your machine, at `127.0.0.1:9090/echo`. It answers with a JSON object that holds the body you sent, and a service `id`.

Send it this JSON body, with the header `Content-Type: application/json`:

```
{"lab":"net-1","msg":"hello"}
```

Save the reply of the service in the file `reply.json` in your home folder.

Hint: `curl -X POST -H 'Header: value' -d 'body' URL`. The URL is `http://127.0.0.1:9090/echo`. If the service answers with an error, check the header.
