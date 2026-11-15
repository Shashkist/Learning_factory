# Solution

```bash
grep ERROR ~/logs/app.log > ~/errors.txt
cat ~/errors.txt
```

`grep ERROR` keeps only the lines with the word `ERROR`. The `>` writes them into `errors.txt`. You should see four lines.
