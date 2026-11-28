# Pass or fail? if and else

Write a Python script `~/grade.py` that decides whether a grade is a pass.

1. `python3 ~/grade.py N` prints `pass` when the grade `N` is `60` or more.
2. It prints `fail` when the grade is below `60`.
3. It prints only that one word and ends normally.

Try it: `python3 ~/grade.py 75` prints `pass`, and `python3 ~/grade.py 40` prints `fail`.

Hint: the words you type after the script name wait in `sys.argv` (after `import sys`, the first one is `sys.argv[1]`). They arrive as text, and text is not a number, so turn it into one with `int(...)` before you compare it with `60`.
