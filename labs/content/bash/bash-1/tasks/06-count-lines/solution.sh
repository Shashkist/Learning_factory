#!/bin/sh
cat > ~/count_lines.sh <<'EOS'
#!/bin/bash
n=0
while read -r line || [ -n "$line" ]; do
  n=$((n + 1))
done < "$1"
echo "$n"
EOS
chmod +x ~/count_lines.sh
