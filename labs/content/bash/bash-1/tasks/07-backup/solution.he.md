# פתרון

```bash
cat > ~/backup.sh <<'EOF'
#!/bin/bash
tar -czf ~/backup.tar.gz -C ~ notes
EOF
chmod +x ~/backup.sh
~/backup.sh
tar tzf ~/backup.tar.gz
```

`-C ~` גורם ל-`tar` לעבור קודם לתיקיית הבית, ולכן הוא שומר `notes/todo.txt` ולא `/home/student/notes/todo.txt`. `-czf` יוצר ארכיון דחוס ב-gzip, ו-`tar` כותב קובץ חדש על הישן, ולכן הרצה שנייה מחליפה את הארכיון.
