# פתרון

```bash
cat > ~/leap.py <<'EOF'
import sys

year = int(sys.argv[1])
if year % 400 == 0:
    print("leap")
elif year % 100 == 0:
    print("not leap")
elif year % 4 == 0:
    print("leap")
else:
    print("not leap")
EOF
python3 ~/leap.py 1900
python3 ~/leap.py 2000
```

הכללים נבדקים מהספציפי ביותר לכללי ביותר. שנה שמתחלקת ב-`400` היא תמיד מעוברת (`2000`). אחרת, שנה שמתחלקת ב-`100` היא לא מעוברת (`1900`). אחרת, שנה שמתחלקת ב-`4` היא מעוברת. כל השאר לא. אותו כלל בשורה אחת הוא `year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)`.
