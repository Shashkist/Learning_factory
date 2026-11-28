#!/bin/sh
cat > ~/add.py <<'EOS'
import sys

a = int(sys.argv[1])
b = int(sys.argv[2])
print(a + b)
EOS
