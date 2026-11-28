# Solution

```bash
cat > ~/even.py <<'EOF'
import sys

n = int(sys.argv[1])
if n % 2 == 0:
    print("even")
else:
    print("odd")
EOF
python3 ~/even.py 10
python3 ~/even.py -3
```

`n % 2` is the remainder of dividing by `2`: `0` for an even number and `1` for an odd one. In Python the remainder of a division by `2` is never negative, so `-3 % 2` is `1` and the script says `odd`. Test `== 0` rather than `== 1`, and the script needs no special case for negative numbers.
