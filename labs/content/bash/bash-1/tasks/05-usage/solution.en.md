# Solution

```bash
cat > ~/usage.sh <<'EOF'
#!/bin/bash
if [ $# -eq 0 ]; then
  echo "Usage: usage.sh NAME"
  exit 1
fi
exit 0
EOF
chmod +x ~/usage.sh
~/usage.sh; echo $?
~/usage.sh Dana; echo $?
```

`$#` counts the arguments, so `[ $# -eq 0 ]` catches the call with no argument. By convention an exit code of `0` means success and anything else means an error. `echo $?` prints the exit code of the last command, so run it right after the script.
