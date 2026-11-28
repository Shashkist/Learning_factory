# פתרון

```bash
cat /etc/resolv.conf
awk '$1 == "nameserver" {print $2; exit}' /etc/resolv.conf > ~/nameserver.txt
cat ~/nameserver.txt
```

שורה כמו `nameserver 192.0.2.53` מציינת שרת DNS: הכתובת היא המילה השנייה. המכונה שואלת את השרתים לפי סדר השורות, ולכן הראשון הוא זה שעונה ראשון. הפקודה `awk` מדפיסה את המילה השנייה של שורת ה-`nameserver` הראשונה ועוצרת. גם הקלדת הכתובת ידנית עם `echo` מתאימה.
