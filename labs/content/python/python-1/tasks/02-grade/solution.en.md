# Solution

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

`sys.argv[1]` is the text the student typed, so `int(...)` turns it into a number. Comparing text with a number would raise an error, and comparing text with text sorts it like words (`"9"` would come after `"60"`). The condition is `>= 60`, not `> 60`: a grade of exactly `60` passes.
