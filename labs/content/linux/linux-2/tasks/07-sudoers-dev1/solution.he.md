# פתרון

```bash
echo 'dev1 ALL=(root) /usr/bin/systemctl status *' | sudo tee /etc/sudoers.d/dev1
sudo chmod 0440 /etc/sudoers.d/dev1
sudo visudo -cf /etc/sudoers.d/dev1
sudo -l -U dev1
```

הכלל אומר: למשתמש `dev1`, בכל host, מותר להריץ בתור `root` בדיוק את הפקודה `/usr/bin/systemctl status *` (ה-`*` מתאים לכל ארגומנט אחרי `status`). `sudo` מתעלם מקבצים שאחרים יכולים לכתוב אליהם, ולכן ההרשאות חייבות להיות `0440`. `visudo -cf` בודק רק תחביר ומדפיס `parsed OK`. `sudo -l -U dev1` מציג מה `dev1` רשאי להריץ: שורה אחת, ושום דבר כמו `ALL` או `systemctl *`.
