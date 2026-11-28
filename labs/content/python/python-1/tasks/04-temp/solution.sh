#!/bin/sh
cat > ~/temp.py <<'EOS'
import sys

c = float(sys.argv[1])
f = c * 9 / 5 + 32
print(f"{round(f, 1):.1f}")
EOS
