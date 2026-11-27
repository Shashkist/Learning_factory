#!/bin/sh
cat > ~/firstline.sh <<'EOS'
#!/bin/bash
head -n 1 "$1"
EOS
chmod +x ~/firstline.sh
