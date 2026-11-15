"""The panels beside the terminal: registers, and a live disassembly around pc.

The panels read only latched state (`--LS_*` on the root) and sit outside the decode containers,
so the CPU's own restyling never reaches them. Each line inherits just the values it shows: a
line whose values didn't change keeps its style and its text isn't laid out again.

ROM never changes, so each ROM word's disassembly is known at build time. Two `@counter-style`s
hold it, indexed by word: `fn` gives the enclosing symbol and `dis` the instruction. A counter set
from pc / 4 picks the entry to show.
"""
import re

from sim import machine as M

from . import visuals
from .expr import V, bit

WORDS = M.ROM_SIZE // 4
BEFORE, AFTER = 3, 6
LINE = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(\S+)\s*(.*)$")
LABEL = re.compile(r"^[0-9a-f]+ <(.+)>:$")
VAR = re.compile(r"var\(--([\w-]+)\)")


def S(name):
    return V("LS_" + name)


def line(cls, counters, content):
    """A panel line: counters are reset on the line and drawn by its ::after."""
    names = sorted(set(VAR.findall(counters)))
    inherit = "".join(f"--{n}:inherit;" for n in names)
    return f".{cls}{{{inherit}counter-reset:{counters}}}.{cls}::after{{content:{content}}}", set(names)


def inherit_rule(selector, names):
    return f"{selector}{{" + ";".join(f"--{n}:inherit" for n in sorted(names)) + "}"


def registers():
    css, rows, used = [], [], set()
    css.append("@counter-style h4{system:numeric;symbols:'0' '1' '2' '3' '4' '5' '6' '7' '8' '9' "
               "'a' 'b' 'c' 'd' 'e' 'f';pad:4 '0'}")
    hex4 = lambda n: "".join(f" counter(r{p},h4)" for p in range(n))

    def add(label, limbs):
        counters = " ".join(f"r{p} {v}" for p, v in enumerate(limbs))
        n = len(rows) - 1
        rule, names = line(f"g{n}", counters, f'"{label:<4}"' + hex4(len(limbs)))
        css.append(rule)
        used.update(names)
        rows.append(f"<p class=g{n}></p>")

    # A bar behind the register the last instruction wrote: x0-x30 are lines 0-30, and either
    # stack pointer shows on the sp line. The two columns hold 18 and 17 lines.
    lw = S("lw")
    line_no = f"min({lw}, 31)"
    right = f"clamp(0, {line_no} - 17, 1)"
    css.append(f".rhl{{--LS_lw:inherit;position:absolute;z-index:-1;width:158px;height:15px;background:#24492f;"
               f"left:calc(10px + {right} * 172px);top:calc(10px + ({line_no} - 18 * {right}) * 15px);"
               f"opacity:clamp(0, 34 - {lw}, 1)}}")
    used.add("LS_lw")
    rows.append("<i class=rhl></i>")
    for i in range(31):
        add(f"x{i}", [S(f"x{i}_{k}") for k in (3, 2, 1, 0)])
    # The current stack pointer: SP_EL1 when at EL1 with SPSel set, else SP_EL0.
    cur = f"{S('el')} * {S('spsel')}"
    add("sp", [f"calc({S(f'sp0_{k}')} + {cur} * ({S(f'sp1_{k}')} - {S(f'sp0_{k}')}))" for k in (3, 2, 1, 0)])
    add("pc", [S("pc1"), S("pc0")])
    add("cnt", [S("cnt1"), S("cnt0")])
    flags = " ".join(f"f{i} calc({bit(S('nzcv'), 3 - i)})" for i in range(4))
    rule, names = line("g99", f"{flags} e {S('el')}",
                       "\"nzcv \" counter(f0) counter(f1) counter(f2) counter(f3) \"  el \" counter(e)")
    css.append(rule)
    used.update(names)
    rows.append("<p class=g99></p>")
    css.append(inherit_rule(".regs", used))
    return css, "".join(rows), used


def parse(listing):
    """Returns (fn, dis): per ROM word, the symbol it belongs to and its disassembly text."""
    fn, dis = [""] * WORDS, [""] * WORDS
    current = ""
    for text in listing.splitlines():
        if m := LABEL.match(text):
            current = m.group(1)
            continue
        m = LINE.match(text)
        if not m:
            continue
        addr = int(m.group(1), 16)
        if addr % 4 or addr // 4 >= WORDS:
            continue
        mnem, ops = m.group(2), m.group(3)
        ops = re.sub(r"\s*//.*$", "", ops)            # objdump's "// #1" comments
        ops = re.sub(r"\s*<[^>]*>", "", ops)          # "<fn+0x1c>" after branch targets
        ops = re.sub(r",\s*", ", ", ops.replace("\t", " ")).strip()
        fn[addr // 4] = current
        dis[addr // 4] = f"{addr:04x}  {mnem:<6} {ops}"[:38]
    return fn, dis


def css_string(s):
    return '"' + s.replace("\\", "\\5c ").replace('"', "\\22 ") + '"'


def disassembly(listing):
    fn, dis = parse(listing)
    css, used = [], set()
    css.append("@counter-style fn{system:fixed 0;symbols:" + " ".join(css_string(s or " ") for s in fn)
               + ";fallback:none}")
    css.append("@counter-style dis{system:fixed 0;symbols:" + " ".join(css_string(s or " ") for s in dis)
               + ";fallback:none}")
    css.append("@counter-style mode{system:fixed 0;symbols:'task  ' 'kernel';fallback:none}")
    css.append("@counter-style irq{system:fixed 0;symbols:' ' '   irq';fallback:none}")
    lines = [
        ("fnm", f"d calc({S('pc0')} / 4) m {S('spsel')}", "counter(m,mode) '  ' counter(d,fn)"),
        # Step count, the timer's countdown to the next tick (0 once it fires), and the GIC state.
        ("stat", f"s calc({S('cnt1')} * 65536 + {S('cnt0')}) t calc({S('tval0')} * (1 - min(1, {S('tval1')}))) "
                 f"q {S('gact')}", "'step ' counter(s) '   next tick ' counter(t) counter(q,irq)"),
    ]
    for k in range(-BEFORE, AFTER + 1):
        lines.append((f"dl{k + BEFORE}", f"d calc({S('pc0')} / 4 + {k})", "counter(d,dis)"))
    rows = []
    for cls, counters, content in lines:
        rule, names = line(cls, counters, content)
        css.append(rule)
        used |= names
        extra = " now" if cls == f"dl{BEFORE}" else ""
        rows.append(f"<p class='{cls}{extra}'></p>")
    css.append(inherit_rule(".dis", used))
    return css, "".join(rows), used


def panels(listing, symbols, tick_steps):
    rcss, regs, rused = registers()
    dcss, dis, dused = disassembly(listing)
    ccss, cache_html, cused, cmarks = visuals.cache()
    icss, issue_html, iused = visuals.issue()
    vcss, vis, vused = visuals.visuals(parse(listing)[0], symbols, tick_steps, cmarks, cused)
    css = rcss + dcss + vcss + ccss + icss + [inherit_rule(".ui", rused | dused | vused | cused | iused)]
    return css, (f"<div class=ui><div class=regs>{regs}</div><div class=dis>{dis}</div>{vis}{cache_html}"
                 f"{issue_html}</div>")
