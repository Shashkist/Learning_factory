# Create a group and add a member

The operations team needs its own group.

1. Create the group `ops`.
2. Make the user `dev1` a member of `ops`.

`dev1` already belongs to another group, set up for you. Run `id dev1` before you change anything and again when you finish, and compare the two lists of groups.

Hint: `groupadd` creates a group, and `usermod` changes a user. Read `man usermod`, especially the part about groups.
