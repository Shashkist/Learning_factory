# פתרון

```bash
cat > ~/add.py <<'EOF'
import sys

a = int(sys.argv[1])
b = int(sys.argv[2])
print(a + b)
EOF
python3 ~/add.py 3 4
python3 ~/add.py -2 9
```

המלכודת: כל מה שב-`sys.argv` הוא string, גם כשזה נראה כמו מספר. עם `print(sys.argv[1] + sys.argv[2])` ה-`+` מדביק שני strings, ולכן `3` ו-`4` מדפיסים `34` ולא `7`. `int(...)` הופך כל string למספר שלם, ורק אז `+` מחבר. בכל פעם שמספר מגיע משורת הפקודה, מקובץ או מ-`input()`, הוא string עד שממירים אותו.
