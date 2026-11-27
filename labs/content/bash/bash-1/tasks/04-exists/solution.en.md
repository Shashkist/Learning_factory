# Solution

```bash
cat > ~/exists.sh <<'EOF'
#!/bin/bash
if [ -e "$1" ]; then
  echo found
else
  echo missing
fi
EOF
chmod +x ~/exists.sh
~/exists.sh /etc
~/exists.sh /no/such/place
```

`[ -e path ]` is true for anything that exists, a file or a folder (`-f` would accept only regular files, and `-d` only folders). The script ends with the exit code of its last command, here `echo`, which is `0`, so both answers exit cleanly.
