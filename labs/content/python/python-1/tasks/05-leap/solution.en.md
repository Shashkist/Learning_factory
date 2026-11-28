# Solution

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

The rules are tested from the most specific to the most general. A year divisible by `400` is always a leap year (`2000`). Otherwise a year divisible by `100` is not (`1900`). Otherwise a year divisible by `4` is. Everything else is not. The same rule in one line is `year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)`.
