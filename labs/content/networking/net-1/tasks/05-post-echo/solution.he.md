# פתרון

```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"lab":"net-1","msg":"hello"}' http://127.0.0.1:9090/echo > ~/reply.json
cat ~/reply.json
```

`-X POST` בוחרת את השיטה, `-H` מוסיפה את הכותרת שאומרת לשירות שהגוף הוא JSON, ו-`-d` הוא הגוף. התשובה נראית כמו `{"id": "...", "echo": {"lab": "net-1", "msg": "hello"}}`: החלק `echo` הוא הגוף שלכם וה-`id` שייך לשירות שרץ.

בלי הכותרת `Content-Type: application/json` השירות עונה `415` עם הודעת `error`, כי הוא לא יודע איך לקרוא את הגוף.
