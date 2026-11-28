# Response headers

Besides the page itself, a web server sends headers: lines such as the status and the type of the content. The server of `~/site` is running on port `8080`.

Save the response headers of the home page `/` in the file `headers.txt` in your home folder. The file must hold only the headers (starting with the status line), without the page itself.

Hint: `curl -I URL` asks for the headers only. The URL looks like `http://127.0.0.1:8080/`. Send the output into the file with `>`.
