# Leap year: conditions that depend on each other

Write a Python script `~/leap.py` that says whether a year is a leap year.

1. `python3 ~/leap.py YEAR` prints `leap` for a leap year and `not leap` for any other year.
2. A year divisible by `4` is a leap year, except a century (like `1900`) that is not divisible by `400`.
3. So `1900` is not a leap year, but `2000` is.

Try it: `python3 ~/leap.py 2024` prints `leap`, and `python3 ~/leap.py 2023` prints `not leap`.

Hint: `and`, `or` and `elif` let you combine conditions. Decide which rule to test first: the exceptions or the general case.
