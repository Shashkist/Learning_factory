# פתרון

```bash
cat > ~/grade.py <<'EOF'
import sys

n = int(sys.argv[1])
if n >= 60:
    print("pass")
else:
    print("fail")
EOF
python3 ~/grade.py 75
python3 ~/grade.py 59
```

`sys.argv[1]` הוא הטקסט שהוקלד, ולכן `int(...)` הופך אותו למספר. השוואה של טקסט למספר גורמת לשגיאה, והשוואה של טקסט לטקסט ממיינת כמו מילים (`"9"` מגיע אחרי `"60"`). התנאי הוא `>= 60` ולא `> 60`: ציון `60` בדיוק עובר.
