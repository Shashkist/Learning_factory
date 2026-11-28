# Solution

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

The trap: everything in `sys.argv` is a string, even when it looks like a number. With `print(sys.argv[1] + sys.argv[2])` the `+` joins two strings, so `3` and `4` print `34`, not `7`. `int(...)` turns each string into a whole number, and then `+` adds. Whenever a number comes from the command line, from a file or from `input()`, it is a string until you convert it.
