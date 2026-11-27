# פתרון

```bash
cat > ~/count_lines.sh <<'EOF'
#!/bin/bash
n=0
while read -r line || [ -n "$line" ]; do
  n=$((n + 1))
done < "$1"
echo "$n"
EOF
chmod +x ~/count_lines.sh
~/count_lines.sh ~/data/fruits.txt
```

`read` מחזיר כישלון בסוף הקובץ, וגם על שורה אחרונה שאין בסופה תו שורה חדשה, למרות שכבר שם את הטקסט שלה ב-`line`. הוספת `|| [ -n "$line" ]` ממשיכה את הלולאה כש-`line` עדיין מחזיק טקסט. `wc -l` סופר תווי שורה חדשה, ולכן על הקובץ הזה הוא היה מדפיס `4` ולא `5`.
