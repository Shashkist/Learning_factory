#!/bin/sh
cat > ~/backup.sh <<'EOS'
#!/bin/bash
tar -czf ~/backup.tar.gz -C ~ notes
EOS
chmod +x ~/backup.sh
