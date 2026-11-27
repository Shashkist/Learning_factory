#!/bin/sh
cat > ~/greet.sh <<'EOS'
#!/bin/bash
echo "Hello, $1"
EOS
chmod +x ~/greet.sh
