# פתרון

```bash
echo '127.0.0.1 web.lab' | sudo tee -a /etc/hosts
getent hosts web.lab
curl -s http://web.lab:8081/ > ~/web-reply.txt
```

בלי `sudo`, הפקודה `echo '127.0.0.1 web.lab' >> /etc/hosts` נכשלת עם `Permission denied`: הקובץ `/etc/hosts` שייך ל-`root`, ול-`student` מותר רק לקרוא אותו. את ההפניה `>>` מבצע ה-shell שלכם, ולכן `sudo` לפני `echo` לא עוזר. הפקודה `sudo tee -a /etc/hosts` מריצה את `tee` כ-root, והדגל `-a` מוסיף לסוף הקובץ במקום לדרוס אותו. גם `sudo sh -c "echo '127.0.0.1 web.lab' >> /etc/hosts"` עובדת.

אחרי זה `getent hosts web.lab` מציג `127.0.0.1       web.lab`, ו-`curl` מוצא את השירות לפי שם. שמרו את הדף עם `curl -s`, בלי `-i`, כדי שבקובץ יהיה רק הדף ולא כותרות ה-HTTP.
