# פתרון

```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/index.html > ~/status-ok.txt
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/nothing-here.html > ~/status-missing.txt
cat ~/status-ok.txt ~/status-missing.txt
```

`-s` מסתיר את פס ההתקדמות, `-o /dev/null` זורק את הדף, ו-`-w '%{http_code}'` מדפיס את קוד הסטטוס בסוף ההעברה. דף שקיים עונה `200` (OK). דף שלא קיים עונה `404` (לא נמצא). כל שם שאין לשרת נותן את אותו `404`.
