# פתרון

```bash
sudo groupadd ops
sudo usermod -aG ops dev1
id dev1
```

`-G ops` קובע שרשימת הקבוצות המשניות היא `ops` בלבד, ולכן כל שאר הקבוצות של המשתמש נמחקות. זאת המלכודת. `-a` אומר append, כלומר הוספה: `usermod -aG ops dev1` מוסיף את `ops` ומשאיר את השאר. גם `sudo gpasswd -a dev1 ops` עושה את אותו דבר. אם כבר איבדתם את הקבוצה הישנה, החזירו אותה עם `sudo usermod -aG webdev dev1`.
