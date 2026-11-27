# פתרון

```bash
sudo useradd -r -s /usr/sbin/nologin svc-app
getent passwd svc-app
ls /home
```

`-s /usr/sbin/nologin` קובע shell שמסרב לכניסות. `-r` יוצר חשבון מערכת, ולחשבון מערכת אין תיקיית בית. גם `sudo useradd -M -s /usr/sbin/nologin svc-app` עובד: `-M` אומר במפורש "בלי תיקיית בית".
