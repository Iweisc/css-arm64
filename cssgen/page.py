"""Builds the HTML page: stylesheet, memory image, terminal, register panel and keyboard.

The clock: every piece of state is the position of a 1px "subject" inside a hidden scroller.
A view timeline on the subject drives an animation on .cpu whose value is that position, so
each frame the CPU sees where the subjects were placed last frame, and places them anew.
"""
import html
import re

from sim import machine as M

from . import cpu, panels, visuals
from .expr import V, eq, floordiv, lt, mod, sel
from .props import REG, Block

H = 65600            # scroller size; every latched value is 0..65535
ROW = 64             # memory cells per row
BANK_ROWS = 8        # memory rows per bank
CW, LH = 10, 18      # terminal character size in px
TG = 4               # terminal characters per cell
NCELLS = (M.ROM_SIZE + M.RAM_SIZE) // 4
RAM_FIRST = M.RAM_BASE // 4


def check_syntax(value):
    """calc() needs spaces around + and -; catch any expression that lost them."""
    v = re.sub(r"var\(--[\w-]+\)", "V", value)
    if re.search(r"\S\+|\+\S", v) or re.search(r"[\w)]-|-[(a-zA-Z]", v):
        raise ValueError(f"bad operator spacing: {value[:200]}")


def css_rule(selector, block):
    for _, v in block.decls:
        check_syntax(v)
    return block.css(selector)


def latch_names():
    """(animated prop, timeline name, initial value) for every latched value: the state as LS_*
    and memory's reads as LR_*. The CPU takes them in as S_* and R_* (see stylesheet). The props
    are registered as <integer>, so the animation's interpolation does the rounding."""
    out = []
    for i in range(0, len(cpu.STATE), 2):
        for axis, (name, init) in zip("ab", cpu.STATE[i:i + 2]):
            out.append((f"LS_{name}", f"--s{i // 2}{axis}", init))
    for r, short in [(f"d{k}", f"d{k}") for k in range(4)]:
        for axis, h in zip("ab", "01"):
            out.append((f"LR_{r}{h}", f"--r{short}{axis}", 0))
    return out


VAR = re.compile(r"var\(--([\w-]+)\)")

# The CPU's lookups are container queries: a container's size is the index it is looked up by
# (one more than the index, in px), and each rule asks for one size. Firefox instead gets the
# index as --q{name}w / --q{name}h and style queries on it, which need no layout: Firefox works
# out a frame of slots in one style pass, several times faster than with size queries. Chrome is
# faster with size queries. Firefox only re-evaluates style queries when something above them
# is re-laid out, not when an animated value changes (all CPU state is animated), so the first
# slot's .pcw is also a size container whose width changes every frame (see stylesheet).
CPU_CONTAINERS = ("rom", "drom", "pa", "pb", "key", "cls", "wa", "wb")
SIZE_QUERY = re.compile(r"@container (" + "|".join(CPU_CONTAINERS) + r") ([^{]+)\{")
FIREFOX = "@supports (-moz-orient:inline)"
NOT_FIREFOX = "@supports not (-moz-orient:inline)"


def box(sel, cont, w, h=None):
    """Rules making `sel` the container `cont`, indexed by w and h (each one more than the index)."""
    size = f"width:calc(({w}) * 1px)" + (f";height:calc(({h}) * 1px)" if h else "")
    index = f"--q{cont}w:calc({w})" + (f";--q{cont}h:calc({h})" if h else "")
    return (f"{NOT_FIREFOX}{{{sel}{{container:{cont}/size;{size}}}}}"
            f"{FIREFOX}{{{sel}{{container-name:{cont};{index}}}}}")


def per_engine_queries(css):
    """Each rule on a CPU container, as a size query outside Firefox and a style query in it."""
    def style(m):
        c = re.sub(r"\((width|height):(\d+)px\)", lambda q: f"style(--q{m.group(1)}{q.group(1)[0]}:{q.group(2)})", m.group(2))
        return f"@container {m.group(1)} {c}{{"
    return [f"{NOT_FIREFOX}{{{rule}}}{FIREFOX}{{{SIZE_QUERY.sub(style, rule)}}}" if SIZE_QUERY.match(rule) else rule
            for rule in css]


def refs(decls):
    """Names a list of (prop, value) declarations reads, and names it defines."""
    used = {n for _, v in decls for n in VAR.findall(str(v))}
    defined = {p[2:] for p, _ in decls if p.startswith("--")}
    return used, defined


def inherit(names):
    return ";".join(f"--{n}:inherit" for n in sorted(names))


def row_latch(prefix, n):
    """CSS for an element that latches `n` cells' two axes: animations of --{prefix}{i}a/b driven by
    view timelines --{prefix}{i}a/b on the cells. Firefox only refreshes a view-timeline-driven
    value every frame when the animated element is an ancestor of the subject's scroller, so the
    row holds its cells' values rather than each cell holding its own."""
    names = [f"--{prefix}{i}{x}" for i in range(n) for x in "ab"]
    for name in names:
        REG.reg(name[2:], True, 0, "<number>")
    keyframes = "".join(f"@keyframes k{name[2:]}{{from{{{name}:{H}}}to{{{name}:-1}}}}" for name in names)
    rule = (f"timeline-scope:{','.join(names)};animation:{','.join(f'k{x[2:]} linear both' for x in names)};"
            f"animation-timeline:{','.join(names)}")
    subject = lambda i: f"view-timeline:--{prefix}{i}a block,--{prefix}{i}b inline"
    return rule, keyframes, subject


def rom_table(cells):
    """Instruction fetch, as a perfect instruction cache: ROM never changes, so fetch happens in
    the same pass with no latency. .pcw is a container as wide as pc's 64-word ROM row, and one
    query per row hands that row's words (F, inherited) to .dcw. .dcw is as tall as pc's column,
    and one query per column hands .st that word as I0 and I1. Two small lookups rather than one
    per word keep the conditions the browser re-checks on every pc change to 128.
    .pcw's height is the bus cell's offset in its 16-word row, for the data window (rom_data_table)."""
    pc0, pc1, ROW = V("S_pc0"), V("S_pc1"), cpu.ROM_ROW
    ok = f"{eq(pc1, 0)} * {eq(mod(pc0, 4), 0)} * {lt(pc0, M.ROM_SIZE)}"
    for c in range(ROW):
        for h in "01":
            REG.reg(f"F{c}{h}", True, 0)
    cell = f"{V('S_bd')} - {cpu.BUS_BIAS['bd']}"
    css = [".pcw{position:absolute;top:0;left:0}",
           box(".pcw", "rom", f"{ok} * ({floordiv(pc0, ROW * 4)} + 1)", f"mod({cell}, {cpu.DROM_ROW}) + 1")]
    # The words are inherited, so .dcw starts from zeros rather than a slot above's row (the
    # rules leave zero words out).
    css.append(".dcw{" + ";".join(f"--F{c}{h}:0" for c in range(ROW) for h in "01") + "}")
    words = M.ROM_SIZE // 4
    for r in range(words // ROW):
        row = cells[r * ROW:(r + 1) * ROW]
        decls = ";".join(f"--F{c}{h}:{v}" for c, w in enumerate(row) for h, v in zip("01", w) if v)
        if decls:
            css.append(f"@container rom (width:{r + 1}px){{.dcw{{{decls}}}}}")
    for c in range(ROW):
        css.append(f"@container drom (height:{c + 1}px){{.st{{--I0:var(--F{c}0);--I1:var(--F{c}1)}}}}")
    return css


def rom_data_table(cells):
    """ROM data for loads: .dcw is a container as wide as the bus cell's 16-word ROM row, and one
    query per row hands .st that row plus the next 3 words (Q, inherited), so any 16-byte window
    starting in the row is there. .pcw's height (rom_table) is the cell's offset in the row, and
    one query per offset hands .pb the window as DQ (cpu.data_window). This keeps ROM out of the
    memory tree entirely. .dcw's height is pc's column, for fetch."""
    R, span = cpu.DROM_ROW, cpu.DROM_SPAN
    words = M.ROM_SIZE // 4
    cell = f"{V('S_bd')} - {cpu.BUS_BIAS['bd']}"
    ok = f"clamp(0, {cell} + 1, 1) * {lt(cell, words)}"
    for i in range(span):
        for h in "01":
            REG.reg(f"Q{i}{h}", True, 0)
    css = [".dcw{position:absolute;top:0;left:0}",
           box(".dcw", "drom", f"{ok} * ({floordiv(cell, R)} + 1)", f"mod({floordiv(V('S_pc0'), 4)}, {cpu.ROM_ROW}) + 1")]
    css.append(".st{" + ";".join(f"--Q{i}{h}:0" for i in range(span) for h in "01") + "}")
    for r in range(words // R):
        span_cells = cells[r * R:min(r * R + span, words)]
        decls = ";".join(f"--Q{i}{h}:{v}" for i, w in enumerate(span_cells) for h, v in zip("01", w) if v)
        if decls:
            css.append(f"@container drom (width:{r + 1}px){{.st{{{decls}}}}}")
    for o in range(R):
        decls = ";".join(f"--DQ{k}{h}:var(--Q{o + k}{h})" for k in range(4) for h in "01")
        css.append(f"@container rom (height:{o + 1}px){{.pb{{{decls}}}}}")
    return css


def register_file():
    """The register file, accessed the way hardware indexes one instead of muxing over every
    register: each port's register number becomes a container size, and one container query per
    register hands over (or takes) that register's limbs, so only the rules for the registers in
    use apply. Returns {element: [(container query or "", declarations)]} in cascade order.

    Reads: .st is sized by rn and rm, so .pb gets A and B; .pb is sized by rd and ra, so .dk gets
    T and U. Writes: .ex is sized by the targets of ports w1 and w3, .sw by w2's, and the subjects'
    scroller .sc takes the written values over the latched ones (cpu.WRITE_ORDER)."""
    out = {".pb": [], ".dk": [], ".sc": []}
    for (pa, _), (pb, _), el, cont in ((cpu.READ_PORTS[0], cpu.READ_PORTS[1], ".pb", "pa"),
                                       (cpu.READ_PORTS[2], cpu.READ_PORTS[3], ".dk", "pb")):
        out[el].append(("", [(f"--{p}{k}", "0") for p in (pa, pb) for k in range(4)]))
        for i in range(31):
            for p, axis in ((pa, "width"), (pb, "height")):
                out[el].append((f"{cont} ({axis}:{i + 1}px)", [(f"--{p}{k}", V(f"S_x{i}_{k}")) for k in range(4)]))
    out[".sc"].append(("", [(f"--N_{r}_{k}", V(f"S_{r}_{k}")) for r, _ in cpu.REGFILE for k in range(4)]))
    port_query = {"w1": "wa (width:{}px)", "w3": "wa (height:{}px)", "w2": "wb (width:{}px)"}
    for w in cpu.WRITE_ORDER:
        for r, n in cpu.REGFILE:
            out[".sc"].append((port_query[w].format(n + 1), [(f"--N_{r}_{k}", V(f"O_{w}v{k}")) for k in range(4)]))
    return out


def stylesheet(rom_cells):
    css = []
    for c in CPU_CONTAINERS:
        for axis in "wh":
            REG.reg(f"q{c}{axis}", False, 0)
    latches = latch_names()
    for a, t, init in latches:
        REG.reg(a, False, init)
    REG.reg("key", False, 0)

    # --- root: one animation per latched value
    css.append(".cpu{position:absolute;top:0;left:0;timeline-scope:" + ",".join(t for _, t, _ in latches)
               + ";animation:" + ",".join(f"k{a} linear both" for a, _, _ in latches)
               + ";animation-timeline:" + ",".join(t for _, t, _ in latches) + "}")
    for a, _, _ in latches:
        css.append(f"@keyframes k{a}{{from{{--{a}:{H}}}to{{--{a}:-1}}}}")

    # --- the CPU levels, outermost first
    st = cpu.cpu_block()
    # Each lookup level and the values it is indexed by (cpu.py's docstring has the levels).
    boxes = [(".dk", "key", V("k1"), V("k2")), (".dc", "cls", V("maj"), V("min")),
             (".st", "pa", V("rn"), V("rm")), (".pb", "pb", V("rd"), V("ra")),
             (".ex", "wa", V("w1t"), V("w3t")), (".sw", "wb", V("w2t"), None)]
    dk = cpu.data_window().decls
    dc = cpu.c_block().decls
    ex_rules = [("", cpu.defaults_block())]
    for maj, mn, blk in cpu.execute_blocks():
        q = f"(width:{maj + 1}px)" + ("" if mn is None else f" and (height:{mn + 1}px)")
        ex_rules.append((q, blk))
    ex_rules += [("", cpu.post_block())]
    ex = [d for _, blk in ex_rules for d in blk.decls]
    rf = register_file()
    rf_decls = lambda el: [d for _, ds in rf[el] for d in ds]
    # The CPU's values are inherited properties inside its own tree, so each level gets what the
    # levels above it computed without passing each one on. They aren't inherited anywhere else:
    # memory and the panels read the root's latched values (LS_*), and only restyle when those
    # they use change. The first slot takes the latched state in as S_* (and memory's reads as
    # R_*). Under every slot but the last, .sc holds the next slot, whose .nx turns this slot's
    # next state into its state and passes on the values carried between slots.
    reads = [(f"R_d{k}{h}", f"LR_d{k}{h}") for k in range(4) for h in "01"]
    take = [(f"S_{n}", f"LS_{n}") for n, _ in cpu.STATE] + reads
    for (name, _), (_, init) in zip(take, cpu.STATE + [(None, 0)] * len(reads)):
        REG.reg(name, True, init)
    css.append(".cpu>.pcw{" + ";".join(f"--{latched}:inherit;--{name}:var(--{latched})" for name, latched in take) + "}")
    # Firefox's clock for its style queries (see box): every frame the first slot either runs an
    # instruction (cnt) or moves a stall on (phase, or boot while booting), so this width changes.
    clock = f"mod({V('S_cnt0')} * 8 + {V('S_phase')} + {V('S_boot')}, 4096) + 1"
    css.append(f"{FIREFOX}{{.cpu>.pcw{{container:rom/size;width:calc(({clock}) * 1px);height:1px}}}}")
    nx = [(f"--S_{n}", V(f"N_{n}")) for n, _ in cpu.STATE] + [(f"--{n}", V(nn)) for n, (nn, _) in cpu.CARRY.items()]
    # Firefox stops at its own issue width: the slots past it aren't rendered (.nxff), and its
    # subjects (.ff) take the state from that slot instead. Chrome hides those subjects. A view
    # timeline name must be declared by only one element, so exactly one set of subjects is
    # rendered. Firefox still styles a display:none element, so .nxff doesn't get .nx's
    # declarations there.
    nx_body = "{position:absolute;top:0;left:0;" + ";".join(f"{p}:{v}" for p, v in nx) + "}"
    if cpu.ISSUE_FIREFOX < cpu.ISSUE:
        css.append("@supports (-moz-orient:inline){.nxff{display:none}.nx:not(.nxff)" + nx_body + "}"
                   "@supports not (-moz-orient:inline){.ff{display:none;view-timeline:none!important}.nx"
                   + nx_body + "}")
    else:
        css.append(".nx" + nx_body)
    fetched = ["I0", "I1"] + [f"DQ{k}{h}" for k in range(4) for h in "01"]
    cpu_decls = st.decls + dk + dc + ex + rf_decls(".sc") + rf_decls(".dk") + rf_decls(".pb") + nx
    REG.inherited |= ({p[2:] for p, _ in cpu_decls if p.startswith("--")} | set(fetched) | set(cpu.CARRY)
                      | {"key"})
    css += rom_table(rom_cells) + rom_data_table(rom_cells)

    css.append(css_rule(".st", st))
    css.append(".st,.pb,.dk,.dc,.ex{position:absolute;top:0;left:0}")
    for sel_, cont, w, h in boxes:
        css.append(box(sel_, cont, f"{w} + 1", f"{h} + 1" if h else None))
    for el, rules in rf.items():
        target = ".sw>.sc" if el == ".sc" else el
        for q, decls in rules:
            rule = f"{target}{{{';'.join(f'{p}:{v}' for p, v in decls)}}}"
            css.append(f"@container {q}{{{rule}}}" if q else rule)
    css.append(".dk{" + ";".join(f"{p}:{v}" for p, v in dk) + "}")
    css += cpu.class_rules()
    cb = Block()
    cb.decls = dc
    css.append(css_rule(".dc", cb))
    for q, blk in ex_rules:
        rule = css_rule(".ex", blk)
        css.append(f"@container cls {q}{{{rule}}}" if q else rule)

    # --- memory and terminal sit beside .st, outside the decode containers (any element under a
    # container that resizes is restyled with it in Firefox), and read the latched bus values.
    bias = cpu.BUS_BIAS
    mb = Block()
    # Memory holds RAM only (ROM is read through the ROM tables), so cells count from RAM's start.
    mb.let("M_dcell", f"{V('LS_bd')} - {bias['bd']} - {RAM_FIRST}")
    mb.let("M_st", f"{V('LS_bst')}")
    masks = f"{V('LS_bsm')}"
    for k in range(4):
        mb.let(f"M_sv{k}lo", f"{V(f'LS_bsv{k}l')}")
        mb.let(f"M_sv{k}hi", f"{V(f'LS_bsv{k}h')}")
        mb.let(f"M_sm{k}", f"mod({floordiv(masks, 16 ** k)}, 16)")
    tb = Block()
    tb.let("T_wrowx", f"{V('LS_bwr')} - {bias['bwr']}")
    tb.let("T_wcolx", f"{V('LS_bwc')}")
    tb.let("T_wchx", f"{V('LS_bch')}")
    tb.let("T_clrx", f"{V('LS_bcl')} - {bias['bcl']}")
    tb.let("T_ffx", f"{V('LS_bff')}")
    for n in ("top", "col", "row"):
        tb.let(f"T_{n}x", f"{V(f'LS_t{n}')}")
    for sel_, blk, kids in ((".mb", mb, ".mb .bus,.mb .sc,.mem,.bank"), (".term", tb, ".scr,.term .sc,.trow,.cur")):
        used, defined = refs(blk.decls)
        css.append(f"{sel_}{{{inherit(used - defined)}}}")
        css.append(css_rule(sel_, blk))
        css.append(f"{kids}{{{inherit(defined)}}}")
    # Banks of rows, like a memory's banks: a bank passes the bus on (as G_*) only when the access
    # falls inside it. Idle banks then compute the same values as last time, and the browser
    # leaves their rows alone instead of restyling every row on every access.
    REG.reg("b", False, 0)
    bk = Block()
    start = f"{V('b')} * {BANK_ROWS * ROW}"
    dcell = V("M_dcell")
    act = bk.let("bact", f"clamp(0, {dcell} + 4 - {start}, 1) * clamp(0, {start} + {BANK_ROWS * ROW} - {dcell}, 1)")
    bank_out = {"dcell": -100, "st": 0} | {f"sv{k}{h}": 0 for k in range(4) for h in ("lo", "hi")} | \
               {f"sm{k}": 0 for k in range(4)}
    for n, idle in bank_out.items():
        bk.let(f"G_{n}", sel(act, V(f"M_{n}"), idle))
    css.append(css_rule(".bank", bk))
    css.append(".bank,.bk2{position:absolute;top:0;left:0}")
    css.append(f".bk2,.row{{{inherit({f'G_{n}' for n in bank_out})}}}")

    # --- hidden scrollers and the state subjects
    css.append(".fx{position:absolute;top:0;left:0;width:0;height:0;contain:strict}")
    css.append(".sw{position:absolute;top:0;left:0;height:0;contain:strict}")
    # Memory also paints the RAM map (visuals.ram_row), so it can't clip to its empty box.
    css.append(".fx.mb{contain:size layout style}")
    css.append(f".sc{{position:absolute;top:0;left:0;width:{H}px;height:{H}px;overflow:hidden}}")
    css.append(".hid{visibility:hidden;pointer-events:none}")
    css.append(":where(.sc i){position:absolute;width:1px;height:1px;top:0;left:0}")
    # Each row is its own scroller, so its cells' self-latching timelines are only recomputed
    # when something in that row moves, not whenever a reader moves.
    css.append(f".rs{{position:absolute;top:0;left:0;width:{H}px;height:{H}px;overflow:hidden}}")
    for i in range(0, len(cpu.STATE), 2):
        (na, _), (nb, _) = cpu.STATE[i:i + 2]
        n = i // 2
        css.append(f".s{n}{{view-timeline:--s{n}a block,--s{n}b inline;--N_{na}:inherit;--N_{nb}:inherit;"
                   f"top:calc({V('N_' + na)} * 1px)!important;left:calc({V('N_' + nb)} * 1px)!important}}")

    # --- memory. Row width selects the fetch column and row height the data window, through
    # container queries that only set anchor-name. Cells must never use explicit `inherit`: that
    # makes Chrome restyle all of a row's cells whenever the row's style changes, which is every
    # step. Store data reaches cells through inherited values that each row gates to itself, so
    # rows an access doesn't touch keep identical values and their cells are left alone.
    REG.reg("j", False, 0)
    REG.reg("col", False, 0)
    REG.reg("ds", False, -100)
    for n in ["dw", "rst"] + [f"g{k}{x}" for k in range(4) for x in "lhm"]:
        REG.reg(n, True, 0)
    for n in ("k", "hit", "Wl", "Wh", "Wm", "o", "q"):
        REG.reg(n, False, 0)
    for n in ("v", "w"):
        REG.reg(n, False, 0, "<number>")
    j, ds, dw, rst = V("j"), V("ds"), V("dw"), V("rst")
    gates = ";".join(f"--g{k}l:calc({rst} * {V(f'G_sv{k}lo')});--g{k}h:calc({rst} * {V(f'G_sv{k}hi')});"
                     f"--g{k}m:calc({rst} * {V(f'G_sm{k}')})" for k in range(4))
    css.append(f".row{{position:absolute;top:0;left:0;container-type:size;"
               f"width:1px;"
               f"--ds:calc({V('G_dcell')} - {j} * {ROW});"
               f"--dw:calc(clamp(0, {ds} + 4, 1) * clamp(0, {ROW} - {ds}, 1) * ({ds} + 4));"
               f"height:calc({dw} * 1px);"
               f"--rst:calc({V('G_st')} * min(1, {dw}));{gates}}}")
    # The selected cells become anchors; a reader per port sits on its anchor and is the
    # view-timeline subject the root reads. (Naming the cell itself as the timeline subject
    # works in Chrome, but Firefox doesn't pick up timeline names set by container queries.)
    an = lambda ports: "anchor-name:" + ",".join(f"--{p}" for p in ports)
    for c in range(ROW):
        css.append(f".c{c}{{--col:{c}}}")
    for s_ in range(-3, ROW):
        body = "".join(f".c{s_ + k}{{{an([f'd{k}'])}}}" for k in range(4) if 0 <= s_ + k < ROW)
        css.append(f"@container (height:{s_ + 4}px){{{body}}}")
    for k in range(4):
        css.append(f".rd{k}{{position-anchor:--d{k};view-timeline:--rd{k}a block,--rd{k}b inline}}")
    css.append(".rd0,.rd1,.rd2,.rd3{top:anchor(top, 0px)!important;left:anchor(left, 0px)!important}")
    # RAM cells are view-timeline subjects whose position is their value, latched by the row's
    # .rl element (see row_latch). A store merges the written bytes (mask Wm) into the old value.
    css += visuals.ram_map_css()
    latch, keyframes, subject = row_latch("m", ROW)
    css.append(f".rl{{position:absolute;top:0;left:0;{latch}}}{keyframes}")
    for c in range(ROW):
        css.append(f".m.c{c}{{{subject(c)};--v:{V(f'm{c}a')};--w:{V(f'm{c}b')}}}")
    o, q, wm, kk = V("o"), V("q"), V("Wm"), V("k")
    sel_g = lambda x: " + ".join(f"max(0, 1 - abs({kk} - {i})) * {V(f'g{i}{x}')}" for i in range(4))
    css.append(f".m{{--o:calc(round({V('v')}));--q:calc(round({V('w')}));"
               f"--k:calc({V('col')} - {dw} + 4);"
               f"--hit:calc({rst} * clamp(0, {kk} + 1, 1) * clamp(0, 4 - {kk}, 1));"
               f"--Wl:calc({sel_g('l')});--Wh:calc({sel_g('h')});--Wm:calc({sel_g('m')});"
               f"top:calc(({o} + {V('hit')} * ({V('Wl')} + (1 - mod({wm}, 2)) * mod({o}, 256)"
               f" + (1 - mod(round(down, {wm} / 2), 2)) * ({o} - mod({o}, 256)) - {o})) * 1px);"
               f"left:calc(({q} + {V('hit')} * ({V('Wh')} + (1 - mod(round(down, {wm} / 4), 2)) * mod({q}, 256)"
               f" + (1 - mod(round(down, {wm} / 8), 2)) * ({q} - mod({q}, 256)) - {q})) * 1px)}}")
    # --- terminal: self-latched cells holding TG characters each, drawn by ::after. A cell keeps
    # two 7-bit characters per scroll axis: fewer cells is less style and layout every frame.
    R, C = M.TERM_ROWS, M.TERM_COLS
    for n in ("twc", "twch", "tcl", "ta", "tb", "tq"):
        REG.reg(n, True, 0)
    REG.reg("tk", False, 0)
    REG.reg("vy", True, 0)
    hit = f"max(0, 1 - abs({V('T_wrowx')} - {j}))"
    latch, keyframes, subject = row_latch("u", C // TG)
    css.append(keyframes)
    css.append(f".trow{{position:absolute;top:0;left:0;{latch};"
               + "".join(f"--u{q}{x}:{32 * 129};" for q in range(C // TG) for x in "ab") +
               f"--twc:calc({hit} * ({V('T_wcolx')} + 1) - 1);--twch:calc({hit} * {V('T_wchx')});"
               f"--tcl:calc(max(max(0, 1 - abs({V('T_clrx')} - {j})), {V('T_ffx')}));"
               f"--vy:calc(mod({j} - {V('T_topx')} + {R}, {R}))}}")
    for q in range(C // TG):
        css.append(f".tq{q}{{--tq:{q};{subject(q)};--v:{V(f'u{q}a')};--w:{V(f'u{q}b')}}}")
    ta, tb, tk, cl, ch = V("ta"), V("tb"), V("tk"), V("tcl"), V("twch")
    lo, hi = (lambda x: f"mod({x}, 128)"), (lambda x: f"round(down, {x} / 128)")
    on = lambda i: f"max(0, 1 - abs({tk} - {i}))"
    blank = 32 * 129

    def axis(x, i):
        """The axis value after a write of character i (low) or i + 1 (high) in this cell, or a clear."""
        kept = f"{x} + {on(i)} * ({ch} - {lo(x)}) + {on(i + 1)} * 128 * ({ch} - {hi(x)})"
        return f"calc(({cl} * {blank} + (1 - {cl}) * ({kept})) * 1px)"

    css.append(f".tc{{--ta:calc(round({V('v')}));--tb:calc(round({V('w')}));--tk:calc({V('twc')} - {V('tq')} * {TG});"
               f"top:{axis(ta, 0)};left:{axis(tb, 2)};"
               f"counter-reset:g0 calc({lo(ta)}) g1 calc({hi(ta)}) g2 calc({lo(tb)}) g3 calc({hi(tb)})}}")
    # The glyphs sit back at the cell's place in the grid, undoing the cell's own offset.
    css.append(f".tc::after{{position:absolute;white-space:pre;letter-spacing:calc({CW}px - 1ch);"
               "content:counter(g0,glyph) counter(g1,glyph) counter(g2,glyph) counter(g3,glyph);"
               f"top:calc(({V('vy')} * {LH} - {ta}) * 1px);left:calc(({V('tq')} * {TG * CW} - {tb}) * 1px)}}")
    syms = []
    for code in range(128):
        ch = chr(code) if 32 <= code < 127 else " "
        syms.append('"\\22"' if ch == '"' else '"\\5c"' if ch == "\\" else f'"{ch}"')
    css.append("@counter-style glyph{system:fixed 0;symbols:" + " ".join(syms) + ";fallback:none}")
    css.append(f".scr{{position:absolute;overflow:hidden;width:{C * CW}px;height:{R * LH}px;"
               f"font:16px/{LH}px ui-monospace,'DejaVu Sans Mono',Menlo,Consolas,monospace;color:#c8f7c5}}")
    css.append(f".cur{{position:absolute;width:{CW}px;height:{LH - 2}px;background:#c8f7c5;"
               f"left:calc({V('T_colx')} * {CW}px);top:calc({V('T_rowx')} * {LH}px + 1px);"
               f"animation:blink 1s steps(2) infinite}}@keyframes blink{{50%{{opacity:0}}}}")
    return per_engine_queries(css)


KEYS = [
    "1234567890-",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm&.",
]


def keyboard():
    css, out = [], []
    for row in KEYS:
        out.append("<div>" + "".join(f"<b class=kb{ord(ch)}>{html.escape(ch)}</b>" for ch in row) + "</div>")
    out.append("<div><b class='kb8 wide'>bksp</b><b class='kb32 space'>space</b><b class='kb10 wide'>enter</b></div>")
    codes = sorted({ord(ch) for row in KEYS for ch in row} | {8, 10, 32})
    for code in codes:
        css.append(f".cpu:has(.kb{code}:active){{--key:{code}}}")
        css.append(f".kb{code}:active{{background:#4a5;color:#000}}")
    return css, "".join(out)


def memory_html(rom_ram, regions):
    """RAM only: row r holds RAM cells r * ROW onwards. ROM is read through the ROM tables."""
    rows = []
    for r in range((NCELLS - RAM_FIRST) // ROW):
        first = RAM_FIRST + r * ROW
        cells = "".join(f"<i class='c{c} m'></i>" for c in range(ROW))
        # The RAM image is the latch's starting value, used until its timelines are live.
        init = "".join(f"--m{c}{x}:{v};" for c in range(ROW) for x, v in zip("ab", rom_ram[first + c]) if v)
        strip = visuals.ram_row(r, regions)
        rows.append(f"<b class=row style=--j:{r}><b class=rl style='{init}'><b class=rs>{cells}</b>{strip}</b></b>")
    banks = "".join(f"<b class=bank style=--b:{i}><b class=bk2>{''.join(rows[i * BANK_ROWS:(i + 1) * BANK_ROWS])}</b></b>"
                    for i in range(len(rows) // BANK_ROWS))
    readers = "".join(f"<i class=rd{k}></i>" for k in range(4))
    return f"<div class='fx mb'><div class='bus'><div class='sc hid'><div class=mem>{banks}</div>{readers}</div></div></div>"


def terminal_html():
    rows = []
    for r in range(M.TERM_ROWS):
        cells = "".join(f"<i class='tc tq{q}'></i>" for q in range(M.TERM_COLS // TG))
        rows.append(f"<b class=trow style=--j:{r}><b class=rs>{cells}</b></b>")
    return f"<div class=term><div class=scr><div class=sc>{''.join(rows)}</div><i class=cur></i></div></div>"


PAGE_CSS = """
html,body{margin:0;background:#101412;color:#9fb8a8;font:14px/1.4 ui-sans-serif,system-ui,sans-serif;overflow:hidden}
.ui{position:absolute;top:0;left:0}
h1{position:absolute;top:14px;left:24px;margin:0;font-size:18px;color:#d8efe0;white-space:nowrap}
h1 small{font-weight:400;color:#7f9a8a;margin-left:10px;font-size:13px}
.term{position:absolute;top:56px;left:24px;width:480px;height:360px;padding:10px;background:#050806;border:1px solid #2a3a30;border-radius:6px}
.term i{font-style:normal}
.regs{position:absolute;isolation:isolate;top:56px;left:540px;width:330px;columns:2;column-gap:14px;
  font:11px/15px ui-monospace,'DejaVu Sans Mono',Menlo,monospace;color:#9fb8a8;
  padding:10px;background:#0b0f0d;border:1px solid #2a3a30;border-radius:6px}
.regs p{margin:0;white-space:pre}
.dis{position:absolute;top:360px;left:540px;width:330px;padding:10px;background:#0b0f0d;border:1px solid #2a3a30;
  border-radius:6px;font:12px/17px ui-monospace,'DejaVu Sans Mono',Menlo,monospace;color:#6f8a7a}
.dis p{margin:0;white-space:pre;overflow:hidden}
.dis .fnm{color:#d8efe0}
.dis .stat{color:#9fb8a8;margin-bottom:6px}
.dis .now{color:#101412;background:#c8f7c5}
.cpu{pointer-events:none}
.kbd{position:absolute;top:470px;left:24px;pointer-events:auto}
.kbd div{display:flex;gap:5px;margin-bottom:5px}
.kbd b{display:block;min-width:34px;padding:7px 0;text-align:center;background:#1d2621;color:#d8efe0;
  border:1px solid #33463b;border-radius:5px;font:14px ui-monospace,monospace;cursor:pointer;user-select:none}
.kbd b.wide{min-width:80px}.kbd b.space{min-width:200px}
.tick{position:absolute;animation:tick 1s steps(60) infinite}@keyframes tick{to{margin-left:60px}}
"""


def build_page(rom, ram, listing, symbols, tick_steps):
    mem = bytes(rom) + bytes(M.ROM_SIZE - len(rom)) + bytes(ram) + bytes(M.RAM_SIZE - len(ram))
    cells = [(mem[i] | mem[i + 1] << 8, mem[i + 2] | mem[i + 3] << 8) for i in range(0, len(mem), 4)]
    pcss, ui = panels.panels(listing, symbols, tick_steps)
    css = stylesheet(cells)
    kcss, keys = keyboard()
    css += pcss + kcss
    subjects = lambda cls: "".join(f"<i class='s{i // 2}{cls}'></i>" for i in range(0, len(cpu.STATE), 2))
    levels = ("pcw", "dcw", "st", "pb", "dk", "dc", "ex", "sw")
    cpu_html = ""
    for slot in reversed(range(cpu.ISSUE)):
        ff = slot == cpu.ISSUE_FIREFOX - 1 < cpu.ISSUE - 1
        inner = subjects("") if slot == cpu.ISSUE - 1 else f"<div class='nx{' nxff' if ff else ''}'>{cpu_html}</div>"
        if ff:
            inner += subjects(" ff")
        cpu_html = ("".join(f"<div class={c}>" for c in levels) + f"<div class='sc hid'>{inner}</div>"
                    + "</div>" * len(levels))
    body = (
        "<div class=cpu>"
        f"{cpu_html}"
        f"{ui}"
        f"{memory_html(cells, visuals.ram_regions(symbols))}{terminal_html()}"
        "<h1>FreeRTOS on an AArch64 CPU made of CSS<small>no JavaScript</small></h1>"
        f"<div class=kbd>{keys}</div><i class=tick></i>"
        "</div>"
    )
    return ("<!doctype html><html lang=en><meta charset=utf-8><title>FreeRTOS on a CSS CPU</title>"
            f"<style>{PAGE_CSS}{visuals.PAGE_CSS}{REG.css()}{''.join(css)}</style><body>{body}</body></html>")
