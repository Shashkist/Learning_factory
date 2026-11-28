# Celsius to Fahrenheit: decimals

Write a Python script `~/temp.py` that converts a temperature from Celsius to Fahrenheit.

1. `python3 ~/temp.py C` prints the temperature in Fahrenheit: `F = C * 9 / 5 + 32`.
2. The result is rounded to one decimal place and always shown with one decimal place.
3. `C` may be a whole number or have a decimal point, like `36.6`.

Try it: `python3 ~/temp.py 100` prints `212.0`, and `python3 ~/temp.py 37` prints `98.6`.

Hint: `round(number, 1)` rounds to one decimal place, and a format such as `f"{value:.1f}"` shows exactly one. A number with a decimal point is a `float`.
