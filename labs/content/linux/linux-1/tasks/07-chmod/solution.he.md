# פתרון

```bash
chmod +x ~/scripts/hello.sh
chmod 600 ~/secret.txt
ls -l ~/scripts ~/secret.txt
```

`+x` מוסיף הרשאת הרצה. `600` אומר: לבעלים 6 (קריאה 4 + כתיבה 2), לקבוצה 0, ולשאר 0. עכשיו `ls -l` מציגה `-rw-------` ליד הסוד.
