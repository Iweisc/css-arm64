"""Small helpers that build CSS calc() expressions over integer values.

Every value the CPU handles is a 16-bit limb or smaller: Firefox evaluates calc() in single
precision, so nothing may exceed 2^24 on the way through an expression.
"""


def V(name):
    return f"var(--{name})"


def floordiv(x, d):
    return f"round(down, ({x}) / {d})"


def mod(x, m):
    return f"mod({x}, {m})"


def bit(x, k):
    """Bit k of a non-negative integer (k may be an expression)."""
    if isinstance(k, int):
        return f"mod(round(down, ({x}) / {1 << k}), 2)"
    return f"mod(round(down, ({x}) / pow(2, {k})), 2)"


def field(x, lo, width):
    """Bits [lo, lo+width) of a non-negative integer."""
    if lo == 0:
        return f"mod({x}, {1 << width})"
    return f"mod(round(down, ({x}) / {1 << lo}), {1 << width})"


def eq(a, b):
    return f"max(0, 1 - abs(({a}) - ({b})))"


def ne(a, b):
    return f"min(1, abs(({a}) - ({b})))"


def ge(a, b):
    """a >= b for integers."""
    return f"clamp(0, ({a}) - ({b}) + 1, 1)"


def lt(a, b):
    return f"clamp(0, ({b}) - ({a}), 1)"


def nz(a):
    """1 if a != 0 (a >= 0)."""
    return f"min(1, {a})"


def iz(a):
    """1 if a == 0 (a >= 0)."""
    return f"(1 - min(1, {a}))"


def sel(c, a, b):
    """c ? a : b, for c in {0, 1}."""
    if a == b:
        return str(a)
    return f"(({c}) * ({a}) + (1 - ({c})) * ({b}))"


def NOT(c):
    return f"(1 - ({c}))"


def AND(*cs):
    return "(" + " * ".join(f"({c})" for c in cs) + ")"


def OR(*cs):
    return "max(" + ", ".join(str(c) for c in cs) + ")"


def onehot_sum(index, options):
    """Selects options[index] by summing eq(index, i) * option; options may contain None (= 0)."""
    terms = [f"{eq(index, i)} * ({o})" for i, o in enumerate(options) if o not in (None, 0, "0")]
    return "calc(" + (" + ".join(terms) if terms else "0") + ")"
