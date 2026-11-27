# פתרון

```bash
cat > ~/usage.sh <<'EOF'
#!/bin/bash
if [ $# -eq 0 ]; then
  echo "Usage: usage.sh NAME"
  exit 1
fi
exit 0
EOF
chmod +x ~/usage.sh
~/usage.sh; echo $?
~/usage.sh Dana; echo $?
```

`$#` סופר את הארגומנטים, ולכן `[ $# -eq 0 ]` תופס את הקריאה בלי ארגומנט. לפי המוסכמה קוד יציאה `0` פירושו הצלחה וכל קוד אחר פירושו שגיאה. `echo $?` מדפיס את קוד היציאה של הפקודה האחרונה, ולכן מריצים אותו מיד אחרי הסקריפט.
