# פתרון

```bash
sleep 600 &
echo $! > ~/sleep.pid
cat ~/sleep.pid
```

`&` מפעיל את הפקודה ברקע ו-`$!` מחזיק את ה-PID שלה. גם `pgrep -x sleep > ~/sleep.pid` עובד כשרץ רק `sleep` אחד. אל תשתמשו ב-`$$`: זה ה-PID של ה-shell שלכם ולא של ה-`sleep`. אם תסגרו את הטרמינל, ה-`sleep` עלול להיעצר איתו, אז השאירו את הטרמינל פתוח, או הפעילו עם `nohup sleep 600 &`.
