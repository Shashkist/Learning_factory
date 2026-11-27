# Solution

```bash
cat > ~/count_lines.sh <<'EOF'
#!/bin/bash
n=0
while read -r line || [ -n "$line" ]; do
  n=$((n + 1))
done < "$1"
echo "$n"
EOF
chmod +x ~/count_lines.sh
~/count_lines.sh ~/data/fruits.txt
```

`read` returns a failure at the end of the file, and it also does so for a last line that has no newline, although it has already put that text in `line`. Adding `|| [ -n "$line" ]` keeps the loop going when `line` still holds text. `wc -l` counts newline characters, so on this file it would print `4`, not `5`.
