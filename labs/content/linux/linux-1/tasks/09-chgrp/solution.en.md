# Solution

```bash
groups
chgrp team ~/team-notes.txt
ls -l ~/team-notes.txt
```

`groups` lists your groups, and `team` is one of them. After `chgrp`, the fourth column of `ls -l` shows `team`, while the owner is still `student`.
