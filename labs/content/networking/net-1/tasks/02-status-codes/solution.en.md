# Solution

```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/index.html > ~/status-ok.txt
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/nothing-here.html > ~/status-missing.txt
cat ~/status-ok.txt ~/status-missing.txt
```

`-s` hides the progress bar, `-o /dev/null` throws the page away, and `-w '%{http_code}'` prints the status code after the transfer. An existing page answers `200` (OK). A page that does not exist answers `404` (not found). Any name that the server does not have gives the same `404`.
