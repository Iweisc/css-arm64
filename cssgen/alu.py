"""Reusable 64-bit datapath pieces. Values are lists of four 16-bit limb expressions, low first.

Each helper writes its intermediate values as custom properties into a Block (so every
sub-expression is evaluated once) and returns var() references to the results.
"""
from .expr import V, bit, eq, floordiv, mod, sel, NOT, ge

M16 = 65535


def limbs(prefix):
    return [V(f"{prefix}{k}") for k in range(4)]


def let4(b, name, exprs):
    return [b.let(f"{name}{k}", e) for k, e in enumerate(exprs)]


def mask_sf(b, name, x, sf):
    """Zeroes the upper 32 bits when sf is 0."""
    return let4(b, name, [x[0], x[1], f"{sf} * {x[2]}", f"{sf} * {x[3]}"])


def inv(x):
    return [f"({M16} - {l})" for l in x]


def add(b, p, a, c, cin, sf):
    """a + c + cin over 64 bits (or 32 when sf=0). Returns (result limbs, N, Z, C, V)."""
    s, r, carry = [], [], cin
    for k in range(4):
        s.append(b.let(f"{p}s{k}", f"{a[k]} + {c[k]} + {carry}"))
        r.append(b.let(f"{p}r{k}", mod(s[k], 65536)))
        carry = b.let(f"{p}c{k + 1}", floordiv(s[k], 65536))
    top_r = b.let(f"{p}tr", sel(sf, r[3], r[1]))
    top_a = b.let(f"{p}ta", sel(sf, a[3], a[1]))
    top_c = b.let(f"{p}tc", sel(sf, c[3], c[1]))
    n = b.let(f"{p}N", bit(top_r, 15))
    z = b.let(f"{p}Z", f"1 - min(1, {r[0]} + {r[1]} + {sf} * ({r[2]} + {r[3]}))")
    cf = b.let(f"{p}C", sel(sf, V(f"{p}c4"), V(f"{p}c2")))
    sa, sc = bit(top_a, 15), bit(top_c, 15)
    v = b.let(f"{p}V", f"{eq(sa, sc)} * {n} * (1 - {sa}) + {eq(sa, sc)} * (1 - {n}) * {sa}")
    res = [r[0], r[1], f"{sf} * {r[2]}", f"{sf} * {r[3]}"]
    return res, n, z, cf, v


def nzcv(n, z, c, v):
    return f"{n} * 8 + {z} * 4 + {c} * 2 + {v}"


def funnel(b, p, lo, hi, s):
    """Bits [s, s+63] of the 128-bit value hi:lo, for 0 <= s <= 64."""
    q = b.let(f"{p}q", floordiv(s, 16))
    r = b.let(f"{p}r", mod(s, 16))
    pw = b.let(f"{p}P", f"pow(2, {r})")
    qw = b.let(f"{p}Q", f"65536 / {pw}")
    cat = list(lo) + list(hi) + ["0"]
    L = []
    for j in range(5):
        terms = [f"{eq(q, qq)} * {cat[j + qq]}" for qq in range(5) if j + qq < 9 and cat[j + qq] != "0"]
        L.append(b.let(f"{p}L{j}", " + ".join(terms) if terms else "0"))
    return [b.let(f"{p}o{j}", f"{floordiv(L[j], pw)} + mod({L[j + 1]}, {pw}) * {qw}") for j in range(4)]


def shift(b, p, x, typ, amt, sf):
    """LSL/LSR/ASR/ROR (typ 0..3) of x by amt (< datasize), in 64 or 32 bits."""
    f64 = b.let(f"{p}f64", f"{bit(x[3], 15)} * {M16}")
    f32 = b.let(f"{p}f32", f"{bit(x[1], 15)} * {M16}")
    t = [b.let(f"{p}t{k}", eq(typ, k)) for k in range(4)]
    up2 = sel(sf, x[2], "0")
    up3 = sel(sf, x[3], "0")
    lo = [
        f"(1 - {t[0]}) * {x[0]}",
        f"(1 - {t[0]}) * {x[1]}",
        f"{t[1]} * {up2} + {t[2]} * {sel(sf, x[2], f32)} + {t[3]} * {sel(sf, x[2], x[0])}",
        f"{t[1]} * {up3} + {t[2]} * {sel(sf, x[3], f32)} + {t[3]} * {sel(sf, x[3], x[1])}",
    ]
    lo = let4(b, f"{p}lo", lo)
    hi = [
        f"{t[0]} * {x[0]} + {t[2]} * {sel(sf, f64, f32)} + {t[3]} * {lo[0]}",
        f"{t[0]} * {x[1]} + {t[2]} * {sel(sf, f64, f32)} + {t[3]} * {lo[1]}",
        f"{t[0]} * {up2} + {t[2]} * {sel(sf, f64, f32)} + {t[3]} * {lo[2]}",
        f"{t[0]} * {up3} + {t[2]} * {sel(sf, f64, f32)} + {t[3]} * {lo[3]}",
    ]
    hi = let4(b, f"{p}hi", hi)
    s = b.let(f"{p}amt", sel(t[0], f"64 - ({amt})", amt))
    out = funnel(b, f"{p}F", lo, hi, s)
    return [out[0], out[1], f"{sf} * {out[2]}", f"{sf} * {out[3]}"]


def lowmask(b, p, x, n):
    """Keeps the low n bits of x (0 <= n <= 64)."""
    out = []
    for k in range(4):
        nk = b.let(f"{p}n{k}", f"clamp(0, {n} - {16 * k}, 16)")
        out.append(b.let(f"{p}m{k}", f"mod({x[k]}, pow(2, {nk}))"))
    return out


def ones(b, p, n):
    return [b.let(f"{p}{k}", f"pow(2, clamp(0, {n} - {16 * k}, 16)) - 1") for k in range(4)]


def band(b, p, x, y):
    """Bitwise AND, limb by limb, from individual bit products."""
    out = []
    for k in range(4):
        terms = [f"{bit(x[k], i)} * {bit(y[k], i)} * {1 << i}" for i in range(16)]
        out.append(b.let(f"{p}{k}", " + ".join(terms)))
    return out


def is_zero(x, sf):
    return f"(1 - min(1, {x[0]} + {x[1]} + {sf} * ({x[2]} + {x[3]})))"


def sext_small(b, p, v, bits):
    """Signed immediate of `bits` bits (< 24) as four limbs."""
    s = b.let(f"{p}sv", f"{v} - {bit(v, bits - 1)} * {1 << bits}")
    l0 = b.let(f"{p}0", mod(s, 65536))
    l1 = b.let(f"{p}1", mod(floordiv(s, 65536), 65536))
    fill = b.let(f"{p}f", f"{bit(v, bits - 1)} * {M16}")
    return [l0, l1, fill, fill]


def cond(c, n, z, cf, v):
    """Evaluates an A64 condition code from flag bits."""
    base = [z, cf, n, v, f"{cf} * (1 - {z})", eq(n, v), f"(1 - {z}) * {eq(n, v)}", "1"]
    pick = " + ".join(f"{eq(floordiv(c, 2), i)} * ({e})" for i, e in enumerate(base))
    inv_ = f"mod({c}, 2) * (1 - {eq(c, 15)})"
    return f"({inv_}) * (1 - ({pick})) + (1 - ({inv_})) * ({pick})"


def add32(b, p, a, c):
    """32-bit wrap-around add of two-limb values."""
    s0 = b.let(f"{p}s0", f"{a[0]} + {c[0]}")
    r0 = b.let(f"{p}0", mod(s0, 65536))
    r1 = b.let(f"{p}1", mod(f"{a[1]} + {c[1]} + {floordiv(s0, 65536)}", 65536))
    return [r0, r1]
