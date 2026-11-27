# Solution

```bash
cat > ~/greet.sh <<'EOF'
#!/bin/bash
echo "Hello, $1"
EOF
chmod +x ~/greet.sh
~/greet.sh Dana
```

The first line, `#!/bin/bash`, tells the system which program runs the script. `$1` is the first argument, so `echo "Hello, $1"` prints the greeting with the name you passed. Without `chmod +x` the shell answers `Permission denied`. A script that ends without an error has exit code `0`; check it with `echo $?` right after running it.
