# פתרון

```bash
curl -sI http://127.0.0.1:8080/ > ~/headers.txt
cat ~/headers.txt
```

`-I` שולחת בקשת HEAD: השרת עונה רק עם הכותרות, בלי הדף. אמורה להופיע שורת סטטוס כמו `HTTP/1.0 200 OK`, אחריה `Content-Type: text/html` ו-`Content-Length`.

דרך אחרת היא `curl -s -D ~/headers.txt -o /dev/null http://127.0.0.1:8080/`: ‏`-D` כותבת לקובץ את הכותרות של בקשה רגילה, ו-`-o /dev/null` זורקת את הדף. `curl -i` לא מתאימה כאן: היא מדפיסה את הכותרות ואת הדף ביחד.
