# פתרון

```bash
sudo useradd -m -s /bin/bash dev1
getent passwd dev1
```

`-m` יוצר את תיקיית הבית (בלעדיו `/home/dev1` לא קיימת) ו-`-s` קובע את ה-shell (ברירת המחדל היא `/bin/sh`). הפלט של `getent passwd dev1` צריך להסתיים ב-`:/home/dev1:/bin/bash`.
