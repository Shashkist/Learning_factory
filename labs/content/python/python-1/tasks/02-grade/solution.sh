#!/bin/sh
cat > ~/grade.py <<'EOS'
import sys

n = int(sys.argv[1])
if n >= 60:
    print("pass")
else:
    print("fail")
EOS
