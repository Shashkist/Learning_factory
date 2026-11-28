# Solution

```bash
curl -sI http://127.0.0.1:8080/ > ~/headers.txt
cat ~/headers.txt
```

`-I` sends a HEAD request: the server answers with the headers only, no page. You should see a status line such as `HTTP/1.0 200 OK`, then `Content-Type: text/html` and `Content-Length`.

Another way is `curl -s -D ~/headers.txt -o /dev/null http://127.0.0.1:8080/`: `-D` writes the headers of a normal request into a file, and `-o /dev/null` throws the page away. `curl -i` is not right here: it prints the headers and the page together.
