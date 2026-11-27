# Solution

```bash
cat > ~/backup.sh <<'EOF'
#!/bin/bash
tar -czf ~/backup.tar.gz -C ~ notes
EOF
chmod +x ~/backup.sh
~/backup.sh
tar tzf ~/backup.tar.gz
```

`-C ~` makes `tar` change to your home folder first, so it stores `notes/todo.txt` and not `/home/student/notes/todo.txt`. `-czf` creates a gzip-compressed archive, and `tar` writes a new file over an old one, so a second run replaces the archive.
