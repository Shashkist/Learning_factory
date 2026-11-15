# Solution

```bash
find ~ -name secret-recipe.txt > ~/found.txt
cat ~/found.txt
```

`find ~` starts the search in your home folder, `-name` says which file name to look for, and `>` saves the answer. The file should hold `/home/student/archive/2023/spring/misc/secret-recipe.txt`.
