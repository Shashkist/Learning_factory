#!/bin/sh
cat > ~/usage.sh <<'EOS'
#!/bin/bash
if [ $# -eq 0 ]; then
  echo "Usage: usage.sh NAME"
  exit 1
fi
exit 0
EOS
chmod +x ~/usage.sh
