# פתרון

```bash
cat > ~/firstline.sh <<'EOF'
#!/bin/bash
head -n 1 "$1"
EOF
chmod +x ~/firstline.sh
~/firstline.sh ~/docs/readme.txt
~/firstline.sh ~/docs/"my notes.txt"
```

המלכודת היא הרווח בשם הקובץ. Bash מרחיב `$1` שאינו במרכאות ואז מפצל את התוצאה בכל רווח, ולכן `head -n 1 $1` רואה את `my` ואת `notes.txt` כשני קבצים שונים ונכשל. כשמקיפים את המשתנה במרכאות כפולות, `"$1"`, שם הקובץ נשאר מילה אחת. כדאי להפוך את זה להרגל: תמיד שמים במרכאות (quotes) כל משתנה שמחזיק שם קובץ או נתיב.
