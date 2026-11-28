# Status codes

Every answer of a web server starts with a three-digit status code. The server of the site in `~/site` is running on port `8080`.

Save two status codes, each in its own file, with only the number inside:

- `~/status-ok.txt`: the status code of the page `/index.html`.
- `~/status-missing.txt`: the status code you get for a page that does not exist on the server.

Hint: `curl -s -o /dev/null -w '%{http_code}' URL` throws the page away and prints only the status code. The URL looks like `http://127.0.0.1:8080/index.html`.
