# Add two numbers

Write a Python script `~/add.py` that adds two whole numbers given on the command line.

1. `python3 ~/add.py A B` prints the sum of `A` and `B`.
2. The numbers may be negative or `0`.
3. It prints only the sum and ends normally.

Try it: `python3 ~/add.py 3 4` should print `7`, and `python3 ~/add.py -2 9` should print `7`.

Hint: the first number is `sys.argv[1]` and the second is `sys.argv[2]` (after `import sys`). Run your script and read what it prints before you say it is done.
