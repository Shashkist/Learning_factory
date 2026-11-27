# פתרון

```bash
cat > ~/exists.sh <<'EOF'
#!/bin/bash
if [ -e "$1" ]; then
  echo found
else
  echo missing
fi
EOF
chmod +x ~/exists.sh
~/exists.sh /etc
~/exists.sh /no/such/place
```

`[ -e path ]` נכון לכל דבר שקיים, קובץ או תיקייה (`-f` מקבל רק קבצים רגילים, ו-`-d` רק תיקיות). הסקריפט מסתיים בקוד היציאה של הפקודה האחרונה שלו, כאן `echo`, שהוא `0`, ולכן שתי התשובות מסתיימות בהצלחה.
