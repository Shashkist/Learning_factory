# פתרון

```bash
curl -s -o /dev/null -w '%{content_type}' http://127.0.0.1:8080/data.json > ~/content-type.txt
cat ~/content-type.txt
```

`-w '%{content_type}'` מדפיסה רק את הערך של הכותרת `Content-Type`, כאן `application/json`. קובץ JSON הוא לא דף אינטרנט, ולכן השרת לא אומר `text/html`.

אותו ערך, שנלקח מהכותרות הגולמיות: `curl -sI http://127.0.0.1:8080/data.json | grep -i '^content-type' | cut -d' ' -f2`.
