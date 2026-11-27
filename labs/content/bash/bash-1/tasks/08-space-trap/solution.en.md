# Solution

```bash
cat > ~/firstline.sh <<'EOF'
#!/bin/bash
head -n 1 "$1"
EOF
chmod +x ~/firstline.sh
~/firstline.sh ~/docs/readme.txt
~/firstline.sh ~/docs/"my notes.txt"
```

The trap is the space in the file name. Bash expands an unquoted `$1` and then splits the result at every space, so `head -n 1 $1` sees `my` and `notes.txt` as two different files and fails. Put double quotes around the variable, `"$1"`, and the file name stays one word. Make it a habit: quote every variable that holds a file name or a path.
