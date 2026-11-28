# Solution

```bash
cat > ~/temp.py <<'EOF'
import sys

c = float(sys.argv[1])
f = c * 9 / 5 + 32
print(f"{round(f, 1):.1f}")
EOF
python3 ~/temp.py 100
python3 ~/temp.py 36.6
```

`float(...)` accepts both `37` and `36.6`, while `int("36.6")` would fail. `round(f, 1)` rounds to one decimal place, and `:.1f` makes the output show exactly one, so `32` is printed as `32.0`. Without the rounding, floating point numbers can print a long tail such as `98.60000000000001`. Use `/` for the division: `9 // 5` would be `1` and ruin the formula.
