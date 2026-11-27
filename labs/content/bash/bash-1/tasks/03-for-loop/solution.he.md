# פתרון

```bash
mkdir -p ~/files
for i in 1 2 3 4 5; do
  touch ~/files/file$i
done
ls ~/files
```

הלולאה רצה חמש פעמים, ובכל פעם `$i` מחזיק את המספר הבא, ולכן `touch` יוצרת את `file1`, אחר כך `file2`, ועד `file5`. הפקודה `mkdir -p` לא מתלוננת כשהתיקייה כבר קיימת. גם `seq 1 5` או `{1..5}` נותנים את אותם מספרים.
