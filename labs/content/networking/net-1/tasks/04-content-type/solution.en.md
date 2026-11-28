# Solution

```bash
curl -s -o /dev/null -w '%{content_type}' http://127.0.0.1:8080/data.json > ~/content-type.txt
cat ~/content-type.txt
```

`-w '%{content_type}'` prints only the value of the `Content-Type` header, here `application/json`. A JSON file is not a web page, so the server does not say `text/html`.

The same value, picked out of the raw headers: `curl -sI http://127.0.0.1:8080/data.json | grep -i '^content-type' | cut -d' ' -f2`.
