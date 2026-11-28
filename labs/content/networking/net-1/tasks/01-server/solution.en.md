# Solution

```bash
cd ~/site
python3 -m http.server 8080 &
curl http://127.0.0.1:8080/index.html
```

`python3 -m http.server 8080` serves the current folder on port `8080`. The `&` runs it in the background, so you get the prompt back. `curl` then asks the server for `index.html`, and you see the page.

The same with one line: `python3 -m http.server 8080 --directory ~/site &`.

If you started the server in the wrong folder, stop it with `kill %1` (or press Ctrl+C when it runs in the foreground) and start it again from `~/site`.
