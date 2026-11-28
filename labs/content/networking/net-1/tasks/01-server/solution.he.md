# פתרון

```bash
cd ~/site
python3 -m http.server 8080 &
curl http://127.0.0.1:8080/index.html
```

הפקודה `python3 -m http.server 8080` מגישה את התיקייה הנוכחית בפורט `8080`. ה-`&` מריץ אותה ברקע, ולכן מקבלים בחזרה את ה-prompt. אחר כך `curl` מבקש מהשרת את `index.html`, ורואים את הדף.

אותו דבר בשורה אחת: `python3 -m http.server 8080 --directory ~/site &`.

אם הפעלתם את השרת בתיקייה הלא נכונה, עוצרים אותו עם `kill %1` (או Ctrl+C כשהוא רץ בחזית) ומפעילים אותו שוב מתוך `~/site`.
