#!/bin/sh
cat > ~/exists.sh <<'EOS'
#!/bin/bash
if [ -e "$1" ]; then
  echo found
else
  echo missing
fi
EOS
chmod +x ~/exists.sh
