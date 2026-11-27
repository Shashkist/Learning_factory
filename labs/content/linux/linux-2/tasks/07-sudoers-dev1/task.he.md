# נותנים פקודה אחת עם sudo

`dev1` צריך להיות מסוגל לצפות במצב של שירותים, ותו לא.

1. צרו את הקובץ `/etc/sudoers.d/dev1`.
2. הוא מאפשר ל-`dev1` להריץ רק את `/usr/bin/systemctl status *` בתור root, דרך `sudo`.
3. הקובץ עובר את `visudo -cf /etc/sudoers.d/dev1`.
4. לקובץ יש הרשאות `0440`.

טעות בקובץ sudoers יכולה לנעול את כולם מחוץ ל-`sudo`, ולכן `visudo` בודק אותו קודם.

רמז: כלל נראה כך: `user host=(runas) command`, למשל `alice ALL=(root) /usr/bin/less /var/log/*`. כתבו את הקובץ עם `sudo tee` או עם `sudo visudo -f /etc/sudoers.d/dev1`, ואז תקנו את ההרשאות עם `chmod`.
