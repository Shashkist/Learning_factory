# פתרון

```bash
cat > ~/greet.sh <<'EOF'
#!/bin/bash
echo "Hello, $1"
EOF
chmod +x ~/greet.sh
~/greet.sh Dana
```

השורה הראשונה, `#!/bin/bash`, אומרת למערכת איזו תוכנה מריצה את הסקריפט. `$1` הוא הארגומנט הראשון, ולכן `echo "Hello, $1"` מדפיס את הברכה עם השם שנמסר. בלי `chmod +x` ה-shell עונה `Permission denied`. סקריפט שמסתיים בלי שגיאה מחזיר קוד יציאה `0`; בודקים את זה עם `echo $?` מיד אחרי ההרצה.
