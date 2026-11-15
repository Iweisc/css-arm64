"""The third column: status lights, a ROM map with pc on it, and a live RAM map.

The ROM map is static: each word is coloured by the function it belongs to, and a marker sits
on pc. The RAM map is drawn by the memory rows themselves (see ram_row), since they already hold
every RAM word's value: each word is a cell, tinted by what that memory is for (stacks, heap,
kernel data) and bright when it isn't zero. Markers show both stack pointers and the bus.
"""
import hashlib
import re

from sim import machine as M

from . import cpu
from .expr import V, eq

X, W = 904, 320          # column left edge and map width; the panels add 10px padding
CELL = 5                 # map cell width in px
ROM_H, RAM_H = 3, 5      # ROM and RAM map row heights
ROW = 64                 # words per map row, the same as memory rows (cssgen.page.ROW)
STATUS_Y = 56
ROM_Y = 134              # ROM map panel top
RAM_Y = 380              # RAM map panel top
CACHE_Y = 576            # data cache panel top
ISSUE_Y = 602            # issue slot panel top (middle column, under the disassembly)
MAP_X, MAP_Y = 10, 30    # where maps sit inside their panel's padding box
RAM_MAP_Y = RAM_Y + 1 + MAP_Y   # the RAM strips are placed on the page, past the panel's border
RAM_WORDS = M.RAM_SIZE // 4
RAM_FIRST = M.RAM_BASE // 4

# name, hue, and how to recognise it in the symbol table
REGIONS = [("kernel", 210, None), ("shell stack", 130, "uxShellStack"), ("idle stack", 175, "uxIdleTaskStack"),
           ("heap", 30, "ucHeap"), ("irq stack", 300, None)]


def S(name):
    return V("LS_" + name)


def inherit(names):
    return "".join(f"--{n}:inherit;" for n in sorted(names))


def ram_regions(symbols):
    """Region index per RAM word: named objects from the symbol table, kernel data for the rest of
    .data/.bss, and the boot/IRQ stack above it up to the end of RAM."""
    out = [len(REGIONS) - 1] * RAM_WORDS
    end = M.RAM_BASE
    named = []
    for line in symbols.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        addr, size, name = int(parts[0], 16), int(parts[1], 16), parts[3]
        if not M.RAM_BASE <= addr < M.RAM_BASE + M.RAM_SIZE:
            continue
        end = max(end, addr + size)
        for i, (_, _, prefix) in enumerate(REGIONS):
            if prefix and name.startswith(prefix):
                named.append((addr, addr + size, i))
    for w in range((end - M.RAM_BASE + 3) // 4):
        out[w] = 0
    for lo, hi, i in named:
        for w in range((lo - M.RAM_BASE) // 4, (hi - M.RAM_BASE + 3) // 4):
            out[w] = i
    return out


def runs(colours, width):
    """A left-to-right gradient of solid runs, one entry per cell."""
    stops, start = [], 0
    for i in range(1, len(colours) + 1):
        if i == len(colours) or colours[i] != colours[start]:
            stops.append(f"{colours[start]} {start * width}px {i * width}px")
            start = i
    return f"linear-gradient(90deg,{','.join(stops)})"


def ram_row(r, regions):
    """The map strip for RAM row r (0-based within RAM): it sits inside the row's latch and reads
    the latched word values the cells use, so it only restyles when a store hits the row."""
    tint = [f"hsl({REGIONS[g][1]} 75% 50%)" for g in regions[r * ROW:(r + 1) * ROW]]
    return f"<b class=mp style='top:{RAM_MAP_Y + r * RAM_H}px;--rg:{runs(tint, CELL)}'></b>"


def ram_map_css():
    # Brightness per word: dark when zero, else brighter with the low byte, so writes flicker.
    shades = []
    for c in range(ROW):
        v, w = V(f"m{c}a"), V(f"m{c}b")
        light = f"calc(11% + min(1, {v} + {w}) * (30% + mod({v}, 256) * 0.16%))"
        shades.append(f"hsl(0 0% {light}) {c * CELL}px {(c + 1) * CELL}px")
    return [
        f".mp{{position:absolute;left:{X + 1 + MAP_X}px;width:{W}px;height:{RAM_H - 1}px;visibility:visible;"
        f"isolation:isolate;background:linear-gradient(90deg,{','.join(shades)})}}",
        # The region's hue over the word's brightness.
        ".mp::after{content:'';position:absolute;inset:0;background:var(--rg);mix-blend-mode:color}",
    ]


def rom_map(fn):
    """Static layers, one per map row, each word coloured by its function."""
    def colour(name):
        if not name:
            return "#0b0f0d"
        h = int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 360
        return f"hsl({h} 45% 34%)"
    layers = []
    for r in range(len(fn) // ROW):
        g = runs([colour(n) for n in fn[r * ROW:(r + 1) * ROW]], CELL)
        layers.append(f"{g} 0 {r * ROM_H}px/{W}px {ROM_H - 1}px no-repeat")
    return ",".join(layers)


def marker(cls, x0, y0, rowh, cell, visible, colour, label, width=1):
    """A box on a map over `width` words from word `cell` (an expression), shown when `visible` is 1."""
    names = set(re.findall(r"var\(--([\w-]+)\)", f"{cell} {visible}"))
    css = (f".{cls}{{{inherit(names)}position:absolute;z-index:1;width:{width * CELL - 2}px;height:{rowh - 2}px;"
           f"border:1px solid {colour};box-shadow:0 0 6px 1px {colour};"
           f"left:calc({x0}px + mod({cell}, {ROW}) * {CELL}px - 1px);"
           f"top:calc({y0}px + round(down, ({cell}) / {ROW}) * {rowh}px - 1px);opacity:{visible}}}"
           f".{cls}::after{{content:'{label}';position:absolute;left:{CELL + 2}px;top:-6px;"
           f"font:10px/12px ui-monospace,monospace;color:{colour}}}")
    return css, f"<i class={cls}></i>", names


def visuals(fn, symbols, tick_steps, cache_marks, cache_names):
    """Returns (css, html, names the .ui must pass down) for the third column. cache_marks are
    the cache's boxes for the ROM and RAM maps (see cache)."""
    css, html, used = [], [], set(cache_names)

    # --- status lights
    leds = [("irq", "IRQ handler active", S("gact"), "#f6c945"),
            ("load", "load", S("phase"), "#5ad1f0"),
            ("store", "store", S("bst"), "#f0645a"),
            ("tx", "terminal write", f"min(1, {S('bch')})", "#c8f7c5"),
            ("key", "keys waiting to be read", f"min(1, {S('kbd')})", "#c08cf5")]
    items = []
    for cls, title, on, colour in leds:
        names = set(re.findall(r"var\(--([\w-]+)\)", on))
        used |= names
        css.append(f".led-{cls}{{{inherit(names)}--on:calc({on});color:{colour};border-color:{colour};"
                   f"opacity:calc(0.22 + 0.78 * var(--on));box-shadow:0 0 calc(var(--on) * 8px) {colour}}}")
        items.append(f"<b class='led led-{cls}' title='{title}'>{cls}</b>")
    # The key light also shows the keyboard FIFO: one pip per key waiting, oldest first.
    codes = [S("kbd")] + [c for n in cpu.KQ for c in (f"mod({S(n)}, 128)", f"round(down, {S(n)} / 128)")]
    pips = []
    for k, code in enumerate(codes[:M.KEY_FIFO]):
        names = set(re.findall(r"var\(--([\w-]+)\)", code))
        used |= names
        css.append(f".kq{k}{{{inherit(names)}opacity:calc(0.25 + 0.75 * min(1, {code}))}}")
        pips.append(f"<i class=kq{k}></i>")
    items[-1] = items[-1].replace("key</b>", f"key<span class=kqs>{''.join(pips)}</span></b>")
    # LS_ values don't inherit (cssgen.page), so each level between .leds and the pips passes them on.
    kq_names = {f"LS_{n}" for n in cpu.KQ}
    css.append(f".led-key,.kqs{{{inherit(kq_names)}}}")
    css.append(".kqs{display:inline-flex;gap:2px;margin-left:6px;vertical-align:-1px}"
               ".kqs i{display:block;width:4px;height:9px;border-radius:1px;background:currentColor}")
    # Steps until the next tick, as a bar that empties towards the interrupt.
    left = f"{S('tval0')} * (1 - min(1, {S('tval1')}))"
    used |= {"LS_tval0", "LS_tval1"}
    css.append(f".tbar i{{{inherit({'S_tval0', 'S_tval1'})}position:absolute;inset:0 auto 0 0;background:#3c6;"
               f"width:calc(min(1, ({left}) / {tick_steps}) * 100%)}}")
    css.append(f".hw{{top:{STATUS_Y}px;height:52px}}")
    html.append(f"<div class='col3 hw'><div class=leds>{''.join(items)}</div>"
                "<div class=tlabel>next tick<span class=tbar><i></i></span></div></div>")

    # --- ROM map and pc
    css.append(f".romp{{top:{ROM_Y}px;height:{20 + (len(fn) // ROW) * ROM_H}px}}"
               f".romm{{position:absolute;left:{MAP_X}px;top:{MAP_Y}px;width:{W}px;height:{(len(fn) // ROW) * ROM_H}px;"
               f"background:{rom_map(fn)}}}")
    pc = f"{S('pc0')} / 4"
    mcss, mhtml, names = marker("pcm", MAP_X, MAP_Y, ROM_H, f"round(down, {pc})", "1", "#ffffff", "pc")
    css.append(mcss)
    used |= names
    html.append(f"<div class='col3 romp'><p class=mt>rom <span>16 KB, each word coloured by function</span></p>"
                f"<div class=romm></div>{mhtml}{''.join(cache_marks['rom'])}</div>")

    # --- RAM map: the strips themselves are drawn by memory (ram_row); markers go on top
    ram = lambda a: f"({a} - {M.RAM_BASE}) / 4"
    inram = lambda cell: f"clamp(0, ({cell}) + 1, 1) * clamp(0, {RAM_WORDS} - ({cell}), 1)"
    marks = []
    for cls, which, colour, label in (("sp0m", "sp0", "#ffe066", "sp0"), ("sp1m", "sp1", "#ff7bd5", "sp1")):
        cell = f"round(down, {ram(S(f'{which}_0'))})"
        # The stack pointer in use is solid; the other is dim.
        active = f"{S('el')} * {S('spsel')}" if which == "sp1" else f"(1 - {S('el')} * {S('spsel')})"
        vis = f"calc({inram(cell)} * (0.35 + 0.65 * {active}))"
        marks.append(marker(cls, MAP_X, MAP_Y, RAM_H, cell, vis, colour, label))
    bus = f"{S('bd')} - {cpu.BUS_BIAS['bd']} - {RAM_FIRST}"
    for cls, colour, kind, label in (("ldm", "#5ad1f0", f"(1 - {S('bst')})", "load"),
                                     ("stm", "#f0645a", S("bst"), "store")):
        marks.append(marker(cls, MAP_X, MAP_Y, RAM_H, bus, f"calc({inram(bus)} * {kind})", colour, label))
    for mcss, mhtml, names in marks:
        css.append(mcss)
        used |= names
    legend = "".join(f"<b style='color:hsl({h} 75% 62%)'>{n}</b>" for n, h, _ in REGIONS)
    css.append(f".ramp{{top:{RAM_Y}px;height:{20 + (RAM_WORDS // ROW) * RAM_H + 22}px}}"
               f".ramp .legend{{position:absolute;left:10px;top:{30 + (RAM_WORDS // ROW) * RAM_H + 6}px}}")
    html.append(f"<div class='col3 ramp'><p class=mt>ram <span>{M.RAM_SIZE // 1024} KB, bright words are non-zero</span></p>"
                f"{''.join(h for _, h, _ in marks)}{''.join(cache_marks['ram'])}<p class=legend>{legend}</p></div>")

    css.append(f".col3,.leds,.tlabel,.tbar{{{inherit(used)}}}")
    return css, "".join(html), used


def issue():
    """The issue slots: one box per slot, lit for each instruction the last frame ran, and the
    first box that didn't run coloured by why (cpu.WHY): waiting for a load, bus busy, stall."""
    css, used = [], {"LS_isn", "LS_isy"}
    n, y = S("isn"), S("isy")
    colours = {cpu.WHY["load wait"]: "#5ad1f0", cpu.WHY["bus busy"]: "#f0645a", cpu.WHY["stall"]: "#8a8f99"}
    boxes = []
    for k in range(cpu.ISSUE):
        ran = f"clamp(0, {n} - {k}, 1)"
        stop = f"{eq(n, k)} * min(1, {y})"
        why = " + ".join(f"{eq(y, w)} * {i}" for i, w in enumerate(colours))
        css.append(f".is{k}{{--ran:calc({ran});--stop:calc({stop});--why:calc({why})}}")
        boxes.append(f"<i class='isb is{k}'></i>")
    reds = [int(c[1:3], 16) for c in colours.values()]
    greens = [int(c[3:5], 16) for c in colours.values()]
    blues = [int(c[5:7], 16) for c in colours.values()]
    pick = lambda vals: " + ".join(f"max(0, 1 - abs(var(--why) - {i})) * {v}" for i, v in enumerate(vals))
    css.append(f".isb{{{inherit(used)}display:block;width:30px;height:26px;border-radius:4px;border:1px solid #2a3a30;"
               "background:color-mix(in srgb,#7ee08a calc(var(--ran) * 100%),#141a17);"
               f"outline:2px solid rgb(calc({pick(reds)}) calc({pick(greens)}) calc({pick(blues)}) / var(--stop));"
               "outline-offset:-2px;box-shadow:0 0 calc(var(--ran) * 7px) #7ee08a}")
    legend = "".join(f"<b style='color:{c}'>{w}</b>" for w, c in
                     ((w, colours[cpu.WHY[w]]) for w in ("load wait", "bus busy", "stall")))
    # Firefox runs fewer slots (cpu.ISSUE_FIREFOX): it shows only those.
    extra = ",".join(f".is{k}" for k in range(cpu.ISSUE_FIREFOX, cpu.ISSUE))
    if extra:
        css.append(f"@supports (-moz-orient:inline){{{extra},.isc{{display:none}}}}"
                   "@supports not (-moz-orient:inline){.isf{display:none}}")
    css.append(f".isr{{{inherit(used)}}}")
    css.append(f".issue{{{inherit(used)}position:absolute;top:{ISSUE_Y}px;left:540px;width:330px;height:76px;"
               "padding:10px;background:#0b0f0d;border:1px solid #2a3a30;border-radius:6px;contain:strict;"
               "font:11px/15px ui-monospace,'DejaVu Sans Mono',Menlo,monospace;color:#9fb8a8}"
               ".issue .mt{margin:0}.isr{display:flex;gap:7px;margin:8px 0 6px}"
               ".issue .legend b{font-weight:400;margin-right:10px}")
    html = (f"<div class=issue><p class=mt>issue <span><span class=isc>{cpu.ISSUE} slots</span>"
            f"<span class=isf>{cpu.ISSUE_FIREFOX} slot{'s' if cpu.ISSUE_FIREFOX > 1 else ''} in Firefox</span>"
            ", lit = ran last frame</span></p>"
            f"<div class=isr>{''.join(boxes)}</div><p class=legend style='margin:0'>stopped by: {legend}</p></div>")
    return css, html, used


def cache():
    """The data cache: one strip per line, its 16 bytes as tiles lit by value (like the RAM map),
    dim while the line is empty, with a bar behind the line the next miss replaces. A lamp shows
    whether the last load hit, and each line is boxed on the ROM or RAM map where it caches."""
    css, html, used, marks = [], [], set(), {"rom": [], "ram": []}
    tile, top0, pitch = 17, 30, 15
    for L in range(M.DCACHE_LINES):
        t = S(f"dl{L}t")
        stops = []
        for i in range(4):
            for h in "01":
                w = S(f"dl{L}w{i}{h}")
                for k, byte in enumerate((f"mod({w}, 256)", f"round(down, {w} / 256)")):
                    n = i * 4 + int(h) * 2 + k
                    light = f"calc(14% + min(1, {byte}) * (28% + {byte} * 0.2%))"
                    stops.append(f"hsl(190 60% {light}) {n * tile}px {n * tile + tile - 2}px,"
                                 f"transparent {n * tile + tile - 2}px {(n + 1) * tile}px")
        names = set(re.findall(r"var\(--([\w-]+)\)", "".join(stops) + t))
        used |= names
        # A dirty line (holding stores RAM hasn't had yet) gets an orange outline.
        d = S(f"dl{L}d")
        names.add(f"LS_dl{L}d")
        used |= names
        css.append(f".cl{L}{{{inherit(names)}position:absolute;left:30px;top:{top0 + L * pitch}px;width:{16 * tile}px;"
                   f"height:{pitch - 3}px;background:linear-gradient(90deg,{','.join(stops)});"
                   f"outline:2px solid rgb(255 160 60 / calc({d}));outline-offset:1px;"
                   f"opacity:calc(0.25 + 0.75 * min(1, {t}))}}")
        html.append(f"<i class=cl{L}></i>")
        # Where the line is: a box over its four words on the ROM or RAM map.
        cell = f"({t} - 1)"
        on = f"min(1, {t})"
        rom_on = f"calc({on} * clamp(0, {RAM_FIRST} - {cell}, 1))"
        ram_cell = f"({cell} - {RAM_FIRST})"
        for cls, x0, y0, rowh, c, vis in ((f"cmr{L}", MAP_X, MAP_Y, ROM_H, cell, rom_on),
                                          (f"cmm{L}", MAP_X, MAP_Y, RAM_H, ram_cell, f"calc({on} - {rom_on})")):
            mcss, mhtml, n2 = marker(cls, x0, y0, rowh, c, vis, "#7fe3ff", "", width=4)
            css.append(mcss)
            used |= n2
            marks["rom" if cls.startswith("cmr") else "ram"].append(mhtml)
    # The line the next miss replaces.
    css.append(f".cnx{{--LS_dlr:inherit;position:absolute;left:6px;width:{16 * tile + 30}px;height:{pitch}px;"
               f"background:#26304a;border-radius:3px;top:calc({top0 - 2}px + {S('dlr')} * {pitch}px)}}")
    used.add("LS_dlr")
    # Line numbers, drawn once: they never change.
    html.append("".join(f"<b class=cno style='top:{top0 - 2 + L * pitch}px'>{L}</b>" for L in range(M.DCACHE_LINES)))
    css.append(".cno{position:absolute;left:14px;font-weight:400;color:#6f8a7a}")
    lamp = f"calc({S('dlh')})"
    used.add("LS_dlh")
    css.append(f".chit{{--LS_dlh:inherit;--on:{lamp}}}.cmiss{{--LS_dlh:inherit;--on:calc(1 - {S('dlh')})}}"
               ".chit,.cmiss{position:absolute;top:8px;opacity:calc(0.22 + 0.78 * var(--on));"
               "box-shadow:0 0 calc(var(--on) * 8px) currentColor}"
               ".chit{right:62px;color:#7ee08a;border-color:#7ee08a}.cmiss{right:10px;color:#f0645a;border-color:#f0645a}")
    css.append(f".cache{{top:{CACHE_Y}px;height:{top0 - 10 + M.DCACHE_LINES * pitch}px;isolation:isolate;contain:strict}}")
    css.append(f".cache{{{inherit(used)}}}")
    title = f"<p class=mt>cache <span>{M.DCACHE_LINES} lines of 16 B, <b class=dty>dirty</b></span></p>"
    css.append(".dty{font-weight:400;color:#ffa03c}")
    lamps = "<b class='led chit'>hit</b><b class='led cmiss'>miss</b>"
    body = f"<div class='col3 cache'>{title}{lamps}<i class=cnx></i>{''.join(html)}</div>"
    return css, body, used, marks


PAGE_CSS = f"""
.col3{{position:absolute;left:{X}px;width:{W}px;padding:10px;background:#0b0f0d;border:1px solid #2a3a30;
  border-radius:6px;font:11px/15px ui-monospace,'DejaVu Sans Mono',Menlo,monospace;color:#9fb8a8}}
.col3 p{{margin:0;white-space:pre}}
.mt{{color:#d8efe0}}.mt span{{color:#6f8a7a}}
.leds{{display:flex;gap:6px}}
.led{{font-weight:400;padding:1px 7px;border:1px solid;border-radius:9px}}
.tlabel{{margin-top:9px;display:flex;align-items:center;gap:10px}}
.tbar{{position:relative;width:200px;height:6px;background:#17201b;border-radius:3px;overflow:hidden}}
.legend b{{font-weight:400;margin-right:10px}}
"""
