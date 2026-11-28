# Content type

The `Content-Type` header tells the client what kind of data came back. The server of `~/site` is running on port `8080`.

Find the content type of the page `/data.json` and save it in the file `content-type.txt` in your home folder. Write only the value (the part after `Content-Type:`), not the whole header line.

Hint: read the headers with `curl -I URL`, for example `curl -I http://127.0.0.1:8080/data.json`, and pick the value out of the line. `curl` can also print just that value with `-w '%{content_type}'`.
