"""The CPU: state registers, decode, and one rule per instruction class.

Element structure (each level is a size container the next level queries):
  .cpu  one animation per latched value, driven by view timelines
  .st   rounded state, instruction fields, register read ports
  .dk   container "key":  width = insn[31:24] + 1, height = insn[23:21] + 1
  .dc   looks the key up in the class table; container "cls": width = major + 1, height = minor + 1
  .ex   the execute stage: defaults plus the rules for the matched major and minor class
Everything below .ex (state subjects, memory, terminal) only reads .ex's outputs.

No CPU property inherits. In Chrome, any change to an element's inherited custom properties
restyles its whole subtree (thousands of memory cells), even below a reset, so each level
passes on exactly what its descendants use with `--x: inherit` (see cssgen.page).
"""
from sim import machine as M
from sim.decode import CLASSES, TABLE

from . import alu
from .alu import M16, let4
from .expr import V, bit, eq, field, floordiv, ge, lt, mod, ne, sel, NOT, nz, iz
from .props import Block, REG

ROM_ROW = 64         # words per ROM table row (cssgen.page.rom_table)
DROM_ROW = 16        # words per ROM data table row (cssgen.page.rom_data_table)
DROM_SPAN = DROM_ROW + 3   # each data row also carries the next 3 words, so a window never needs two

# Register numbers used by the write ports
SP_CUR, SP_EL0, SP_EL1, NOREG = 31, 32, 33, 99

# --- architectural state, all stored as 16-bit values -------------------------------------------
STATE = []
for i in range(31):
    STATE += [(f"x{i}_{k}", 0) for k in range(4)]
STATE += [(f"sp0_{k}", 0) for k in range(4)] + [(f"sp1_{k}", 0) for k in range(4)]
STATE += [("pc0", 0), ("pc1", 0)]
STATE += [("elr0", 0), ("elr1", 0), ("spsr0", 0), ("spsr1", 0), ("esr0", 0), ("esr1", 0),
          ("vbar0", 0), ("vbar1", 0), ("tval0", 0), ("tval1", 0), ("cnt0", 0), ("cnt1", 0)]
STATE += [("nzcv", 0), ("el", 1), ("spsel", 1), ("daif", 15), ("tctl", 0), ("phase", 0),
          ("kbd", 0), ("kprev", 0), ("tcol", 0), ("trow", 0), ("ttop", 0), ("boot", 6)]
# GIC CPU interface: the priority mask and whether an interrupt is active (see sim.machine)
STATE += [("pmr", 0), ("gact", 0)]
# The bus: what memory and the terminal act on, latched like any other state, so they see it one
# frame after the CPU computes it and can live outside the decode containers (see cssgen.page).
# Values that can be negative are stored with a bias.
BUS_BIAS = {"bd": 16, "bwr": 1, "bcl": 1}
STATE += [("bd", 6), ("bst", 0), ("bsm", 0)] + [(f"bsv{k}{h}", 0) for k in range(4) for h in "lh"]
STATE += [("bwr", 0), ("bwc", 0), ("bch", 0), ("bcl", 0), ("bff", 0)]
# For the register panel only: the last register written (NOREG until the first write).
STATE += [("lw", NOREG)]
# For the issue panel only: how many slots ran last frame, and why the rest didn't (WHY).
STATE += [("isn", 0), ("isy", 0)]
WHY = {"ran all": 0, "load wait": 1, "bus busy": 2, "stall": 3}
# Keys waiting behind kbd, like a UART's FIFO: two 7-bit codes per value (three would pass Chrome's
# 6 significant digits), oldest first and in the low bits.
KQ = [f"kq{j}" for j in range(M.KEY_FIFO // 2)]
STATE += [(n, 0) for n in KQ]
# The data cache (sim.iss.Machine.cached): per line, its first cell + 1 (0 when empty), whether
# it holds stores RAM hasn't had yet (dirty), and its 4 words; the line the next miss fills;
# whether the last load hit.
CACHE = [f"dl{L}{x}" for L in range(M.DCACHE_LINES) for x in "td"] + \
        [f"dl{L}w{i}{h}" for L in range(M.DCACHE_LINES) for i in range(4) for h in "01"] + ["dlr", "dlh"]
STATE += [(n, 0) for n in CACHE]
assert len(STATE) % 2 == 0, "latches hold state in pairs"
STATE_INIT = dict(STATE)

# --- major/minor classes -------------------------------------------------------------------------
GROUPS = {
    "ADDSUB": ["ADDSUB_IMM", "ADDSUB_SREG", "ADDSUB_EREG", "ADC"],
    "LOGIC": ["LOGIC_IMM", "LOGIC_SREG"],
    "MEM": ["LDST_UIMM", "LDST_IMM9", "LDST_REG", "LDR_LIT", "LDSTP"],
    "EXC": ["UNDEF", "SVC", "IRQ"],
}
MAJORS = ["STALL", "ADDSUB", "LOGIC", "MEM", "EXC", "MOVW", "BITFIELD", "EXTR", "ADR", "B", "BCOND",
          "CB", "TB", "SYS", "BR", "CCMP", "CSEL", "DP2", "DP1", "DP3", "HOLD"]

# Issue width: instructions the CPU can complete per frame. Each issue slot is a full copy of the
# decode and execute levels, fed the previous slot's next state (cssgen.page). A slot only runs
# if no earlier slot is waiting for a load and it doesn't need the bus after an earlier slot used it
# (go); otherwise it holds (HOLD) and passes everything through, so memory sees one access per frame.
# Each slot costs Firefox more, so it runs fewer (cssgen.page). Both widths are the measured fastest.
ISSUE = 8
# Values carried from one issue slot to the next within a frame: name -> (next value's name,
# value in the first slot). go: no earlier slot stopped; bz: an earlier slot used the bus;
# ic: slots that ran so far; wy: why the group stopped (WHY), once it has.
CARRY = {"go": ("gon", 1), "bz": ("bzn", 0), "ic": ("icn", 0), "wy": ("wyn", 0)}
ISSUE_FIREFOX = 4
MAJ = {m: i for i, m in enumerate(MAJORS)}


# The register file, indexed like a real one: cssgen.page.register_file turns the read port
# indexes and write port targets into container sizes, and one rule per register does the access.
# Register 31 reads as zero through the ports (classes pick SP when they need it).
READ_PORTS = (("A", "rn"), ("B", "rm"), ("T", "rd"), ("U", "ra"))
REGFILE = [(f"x{i}", i) for i in range(31)] + [("sp0", SP_EL0), ("sp1", SP_EL1)]
WRITE_ORDER = ("w3", "w1", "w2")   # when ports write the same register, the later one wins


def major_minor(cls_name):
    for g, members in GROUPS.items():
        if cls_name in members:
            return MAJ[g], members.index(cls_name)
    return MAJ[cls_name], 0


def S(name):
    return V(f"S_{name}")


# --- .st: state, fields, ports ----------------------------------------------------------------
def cpu_block():
    b = Block()
    # The instruction, I0 and I1, comes from the ROM tables (cssgen.page.rom_table).
    for h in "01":
        REG.reg(f"I{h}", False, 0)
    I0, I1 = V("I0"), V("I1")
    f = {}
    f["rd"] = b.let("rd", mod(I0, 32))
    f["rn"] = b.let("rn", field(I0, 5, 5))
    f["imm6"] = b.let("imm6", field(I0, 10, 6))
    f["ra"] = b.let("ra", field(I0, 10, 5))
    f["rm"] = b.let("rm", mod(I1, 32))
    f["immr"] = b.let("immr", mod(I1, 64))
    f["sf"] = b.let("sf", bit(I1, 15))
    for n in (21, 22, 23, 24, 29, 30):
        f[f"b{n}"] = b.let(f"b{n}", bit(I1, n - 16))
    for n in (10, 11, 12, 15):
        f[f"b{n}"] = b.let(f"b{n}", bit(I0, n))
    f["k1"] = b.let("k1", floordiv(I1, 256))         # insn[31:24]
    f["k2"] = b.let("k2", field(I1, 5, 3))           # insn[23:21]
    f["imm12"] = b.let("imm12", f"{field(I0, 10, 6)} + {mod(I1, 64)} * 64")
    f["imm16"] = b.let("imm16", f"{field(I0, 5, 11)} + {mod(I1, 32)} * 2048")
    f["imm19"] = b.let("imm19", f"{field(I0, 5, 11)} + {mod(I1, 256)} * 2048")
    f["imm14"] = b.let("imm14", f"{field(I0, 5, 11)} + {mod(I1, 8)} * 2048")
    f["imm9"] = b.let("imm9", f"{field(I0, 12, 4)} + {mod(I1, 32)} * 16")
    f["imm7"] = b.let("imm7", f"{field(I0, 15, 1)} + {mod(I1, 64)} * 2")
    f["size"] = b.let("size", field(I1, 14, 2))
    f["opc"] = b.let("opc", field(I1, 6, 2))          # insn[23:22]
    f["opc2"] = b.let("opc2", field(I1, 13, 2))       # insn[30:29]
    f["hw"] = b.let("hw", field(I1, 5, 2))
    f["opt"] = b.let("opt", field(I0, 13, 3))
    f["imm3"] = b.let("imm3", field(I0, 10, 3))
    f["mode"] = b.let("mode", field(I0, 10, 2))
    f["c12"] = b.let("c12", field(I0, 12, 4))
    f["c0"] = b.let("c0", mod(I0, 16))

    # flags
    nzcv = S("nzcv")
    b.let("fN", bit(nzcv, 3))
    b.let("fZ", bit(nzcv, 2))
    b.let("fC", bit(nzcv, 1))
    b.let("fV", bit(nzcv, 0))
    b.let("useSP1", f"{S('el')} * {S('spsel')}")
    for k in range(4):
        b.let(f"SP{k}", sel(V("useSP1"), S(f"sp1_{k}"), S(f"sp0_{k}")))
    # The register read ports A, B, T, U are filled in by cssgen.page.register_file.
    for port, _ in READ_PORTS:
        for k in range(4):
            REG.reg(f"{port}{k}", False, 0)
    # pc + 4
    s0 = b.let("pcs", f"{S('pc0')} + 4")
    b.let("pn0", mod(s0, 65536))
    b.let("pn1", mod(f"{S('pc1')} + {floordiv(s0, 65536)}", 65536))
    # Fetch reads the ROM table in the same pass, so it is always valid. The first few frames
    # still stall: the data readers' timelines can start a frame later than the state's. A load
    # waits one frame (phase 1) while memory acts on its address; see mem_common.
    b.let("fv", f"{iz(S('boot'))} * (1 - {eq(S('phase'), 1)})")
    for name, (_, initial) in CARRY.items():
        REG.reg(name, False, initial)
    # interrupts: the timer and the keyboard, through the GIC
    tv_le0 = f"max({ge(S('tval1'), 32768)}, {eq(S('tval0') + ' + ' + S('tval1'), 0)})"
    b.let("tist", tv_le0)
    b.let("tasrt", f"{bit(S('tctl'), 0)} * (1 - {bit(S('tctl'), 1)}) * {V('tist')}")
    b.let("gpend", f"max({V('tasrt')}, {nz(S('kbd'))}) * {ge(S('pmr'), M.TIMER_PRIO + 1)} * (1 - {S('gact')})")
    b.let("irq", f"{V('gpend')} * (1 - {bit(S('daif'), 1)})")
    return b


def data_window():
    """D{k}{h}: the four words a load reads, starting at the cell on the bus. ROM words come from
    the ROM data table (cssgen.page.rom_data_table) as DQ in this same pass; RAM words come from
    the memory's data readers (R_d)."""
    b = Block()
    for k in range(4):
        for h in "01":
            REG.reg(f"DQ{k}{h}", False, 0)
    cell = b.let("dwc", f"{S('bd')} - {BUS_BIAS['bd']}")
    words = M.ROM_SIZE // 4
    for k in range(4):
        inrom = f"clamp(0, {cell} + {k} + 1, 1) * {lt(f'{cell} + {k}', words)}"
        for h in "01":
            b.let(f"D{k}{h}", sel(inrom, V(f"DQ{k}{h}"), V(f"R_d{k}{h}")))
    return b



# --- .dc: class lookup --------------------------------------------------------------------------
def class_rules():
    """Rules mapping (insn[31:24], insn[23:21]) to (major, minor), as compactly as possible."""
    rules = []
    for top in range(256):
        subs = [major_minor(CLASSES[TABLE[(top << 3) | s]]) for s in range(8)]
        if all(x == subs[0] for x in subs):
            if subs[0] != (MAJ["EXC"], 0):
                rules.append((f"(width:{top + 1}px)", subs[0]))
        else:
            for s, mm in enumerate(subs):
                if mm != (MAJ["EXC"], 0):
                    rules.append((f"(width:{top + 1}px) and (height:{s + 1}px)", mm))
    css = []
    for q, (maj, mn) in rules:
        css.append(f"@container key {q}{{.dc{{--tmaj:{maj};--tmin:{mn}}}}}")
    return css


def c_block():
    b = Block()
    REG.reg("tmaj", False, MAJ["EXC"])
    REG.reg("tmin", False, 0)
    tmaj, tmin, imm6, sf = V("tmaj"), V("tmin"), V("imm6"), V("sf")
    # Sub-encodings the table can't see: these raise an undefined-instruction exception.
    dp2_ok = f"max({eq(imm6, 2)}, {eq(imm6, 3)}, {eq(imm6, 8)}, {eq(imm6, 9)}, {eq(imm6, 10)}, {eq(imm6, 11)})"
    dp1_ok = f"{ge(5, imm6)} * (1 - (1 - {sf}) * {eq(imm6, 3)})"
    op31, o0 = field(V("I1"), 5, 3), V("b15")
    dp3_ok = (f"max({eq(op31, 0)}, {sf} * max({eq(op31, 1)}, {eq(op31, 5)}), "
              f"{sf} * (1 - {o0}) * max({eq(op31, 2)}, {eq(op31, 6)}))")
    bad = b.let("bad", f"{eq(tmaj, MAJ['DP2'])} * (1 - {dp2_ok}) + {eq(tmaj, MAJ['DP1'])} * (1 - {dp1_ok})"
                       f" + {eq(tmaj, MAJ['DP3'])} * (1 - {dp3_ok})")
    stall = f"(1 - {V('fv')})"
    irq = f"{V('fv')} * {iz(S('phase'))} * {V('irq')}"
    held = f"(1 - {V('go')})"
    maj = sel(held, MAJ["HOLD"], sel(stall, MAJ["STALL"], sel(irq, MAJ["EXC"], sel(bad, MAJ["EXC"], tmaj))))
    mn = sel(f"max({held}, {stall})", 0, sel(irq, GROUPS["EXC"].index("IRQ"), sel(bad, 0, tmin)))
    b.let("maj", maj)
    b.let("min", mn)
    return b


# --- .ex: defaults --------------------------------------------------------------------------------
OUT_DEFAULTS = {}


def out(b, name, expr, default):
    OUT_DEFAULTS[name] = default
    return b.let(f"O_{name}", expr)


def defaults_block():
    """Everything holds and pc advances; the class rules override what they change."""
    b = Block()
    d = {
        "w1i": NOREG, "w2i": NOREG, "w3i": NOREG,
        "npc0": V("pn0"), "npc1": V("pn1"),
        "nzcv": S("nzcv"), "el": S("el"), "spsel": S("spsel"), "daif": S("daif"), "phase": 0,
        "elr0": S("elr0"), "elr1": S("elr1"), "spsr0": S("spsr0"), "spsr1": S("spsr1"),
        "esr0": S("esr0"), "esr1": S("esr1"), "vbar0": S("vbar0"), "vbar1": S("vbar1"),
        "tvw": 0, "tv0": 0, "tv1": 0, "tctl": S("tctl"), "tx": -1, "rxrd": 0,
        "pmr": S("pmr"), "gact": S("gact"),
        "dcell": -10, "st": 0,
    }
    for k in range(4):
        for w in ("w1v", "w2v", "w3v"):
            d[f"{w}{k}"] = 0
        d[f"sv{k}lo"] = 0
        d[f"sv{k}hi"] = 0
        d[f"sm{k}"] = 0
    d["hit"] = 0
    d["blk"] = 0
    for name, v in d.items():
        out(b, name, v, v)
    # Only loads and stores change the cache (mem_common).
    for n in CACHE:
        b.let(f"N_{n}", S(n))
    return b


def post_block():
    """Next-state values derived from the outputs, shared by every class.
    Every N_<state> here is where the matching state subject is placed this frame."""
    b = Block()
    O = lambda n: V(f"O_{n}")
    stall = b.let("stall", eq(V("maj"), MAJ["STALL"]))
    # A slot holds when an earlier one stopped, or when it needs the bus and an earlier slot
    # this frame already used it (the bus is a one-entry store buffer, drained next frame).
    hold = b.let("hold", f"max({eq(V('maj'), MAJ['HOLD'])}, {O('blk')})")
    tick = f"(1 - {stall} - {hold})"
    # The next slot runs unless this one is waiting for a load.
    b.let("gon", f"{tick} * {iz(O('phase'))}")
    b.let("bzn", f"max({V('bz')}, {ne(O('dcell'), -10)})")
    icn = b.let("icn", f"{V('ic')} + {tick}")
    why = sel(O("blk"), WHY["bus busy"], sel(stall, WHY["stall"], sel(nz(O("phase")), WHY["load wait"], 0)))
    wyn = b.let("wyn", sel(nz(V("wy")), V("wy"), why))
    b.let("N_isn", icn)
    b.let("N_isy", wyn)
    # write port targets: 31 means the current stack pointer
    wt = {}
    for w in ("w1", "w2", "w3"):
        wt[w] = b.let(f"{w}t", sel(eq(O(f"{w}i"), SP_CUR), sel(V("useSP1"), SP_EL1, SP_EL0), O(f"{w}i")))
    # The registers' next values are written by cssgen.page.register_file, using the targets.
    for name, _ in REGFILE:
        for k in range(4):
            REG.reg(f"N_{name}_{k}", False, 0)
    b.let("N_pc0", O("npc0"))
    b.let("N_pc1", O("npc1"))
    b.let("N_bd", f"{O('dcell')} + {BUS_BIAS['bd']}")
    b.let("N_bst", O("st"))
    b.let("N_bsm", " + ".join(f"{O(f'sm{k}')} * {16 ** k}" for k in range(4)))
    for k in range(4):
        b.let(f"N_bsv{k}l", O(f"sv{k}lo"))
        b.let(f"N_bsv{k}h", O(f"sv{k}hi"))
    for name in ("elr0", "elr1", "spsr0", "spsr1", "esr0", "esr1", "vbar0", "vbar1",
                 "nzcv", "el", "spsel", "daif", "tctl", "phase", "pmr", "gact"):
        b.let(f"N_{name}", O(name))
    # timer and counter
    d0 = b.let("td0", f"{S('tval0')} - {tick}")
    t0 = mod(d0, 65536)
    t1 = mod(f"{S('tval1')} + {floordiv(d0, 65536)}", 65536)
    b.let("N_tval0", sel(O("tvw"), O("tv0"), t0))
    b.let("N_tval1", sel(O("tvw"), O("tv1"), t1))
    c0 = b.let("cs0", f"{S('cnt0')} + {tick}")
    b.let("N_cnt0", mod(c0, 65536))
    b.let("N_cnt1", mod(f"{S('cnt1')} + {floordiv(c0, 65536)}", 65536))
    # Keyboard FIFO: kbd is the key RX returns and the KQ values hold the rest, packed oldest
    # first. A read from RX moves the queue up; a new key press goes in the first free place, or
    # is dropped when all are full.
    key = V("key")
    edge = b.let("kedge", f"{nz(key)} * {ne(key, S('kprev'))}")
    pop = O("rxrd")
    codes = [S("kbd")]
    for n in KQ:
        codes += [mod(S(n), 128), floordiv(S(n), 128)]
    codes = codes[:M.KEY_FIFO] + ["0"]
    moved = [b.let(f"ka{i}", sel(pop, codes[i + 1], codes[i])) for i in range(M.KEY_FIFO)]
    new = [sel(f"{edge} * {iz(moved[i])}" + (f" * {nz(moved[i - 1])}" if i else ""), key, moved[i])
           for i in range(M.KEY_FIFO)] + ["0"]
    b.let("N_kbd", new[0])
    for j, n in enumerate(KQ):
        b.let(f"N_{n}", f"{new[2 * j + 1]} + 128 * ({new[2 * j + 2]})")
    b.let("N_kprev", key)
    b.let("N_boot", f"max(0, {S('boot')} - 1)")
    b.let("N_lw", sel(ne(wt["w1"], NOREG), wt["w1"], sel(ne(wt["w3"], NOREG), wt["w3"], S("lw"))))
    terminal_post(b)
    # The bus keeps what an earlier slot this frame put on it, unless this slot uses it (it can
    # only when it was free). A held slot also keeps the boot countdown as it was.
    bus = {"bd", "bst", "bsm", "bwr", "bwc", "bch", "bcl", "bff"} | {f"bsv{k}{h}" for k in range(4) for h in "lh"}
    keep_bus = b.let("kbus", f"max({hold}, {V('bz')} * {eq(O('dcell'), -10)})")
    for i, (prop, value) in enumerate(b.decls):
        name = prop[4:]
        if prop.startswith("--N_") and (name in bus or name == "boot"):
            b.decls[i] = (prop, f"calc({sel(keep_bus if name in bus else hold, S(name), value[5:-1])})")
    for name, _ in STATE:
        assert f"N_{name}" in REG.props, name
    return b


def terminal_post(b):
    """Cursor and scroll state for a character written to TX, and the terminal's bus values:
    the cell to write (row bwr - 1, column bwc, char bch), and a row to blank (bcl - 1) or all (bff)."""
    R, C = M.TERM_ROWS, M.TERM_COLS
    c = V("O_tx")
    col, row, top = S("tcol"), S("trow"), S("ttop")
    lf, cr, bs, ff = (b.let(f"t_{n}", eq(c, v)) for n, v in (("lf", 10), ("cr", 13), ("bs", 8), ("ff", 12)))
    pr = b.let("t_pr", f"{ge(c, 32)} * {ge(126, c)}")
    wrap = b.let("t_wrap", f"{pr} * {eq(col + ' + 1', C)}")
    nl = b.let("t_nl", f"max({lf}, {wrap})")
    last = b.let("t_last", eq(row, R - 1))
    scroll = b.let("t_scroll", f"{nl} * {last}")
    newcol = (f"{pr} * (1 - {wrap}) * ({col} + 1) + {bs} * max(0, {col} - 1)"
              f" + (1 - {pr} - {bs} - {lf} - {cr} - {ff}) * {col}")
    b.let("N_tcol", newcol)
    b.let("N_trow", sel(ff, 0, sel(nl, f"{row} + 1 - {last}", row)))
    b.let("N_ttop", sel(ff, 0, sel(scroll, mod(f"{top} + 1", R), top)))
    b.let("N_bwr", f"{sel(pr, mod(f'{top} + {row}', R), -1)} + {BUS_BIAS['bwr']}")
    b.let("N_bwc", col)
    b.let("N_bch", f"{pr} * {c}")
    b.let("N_bcl", f"{sel(scroll, top, -1)} + {BUS_BIAS['bcl']}")
    b.let("N_bff", ff)


# --- class rules ---------------------------------------------------------------------------------
def O(b, name, expr):
    return b.let(f"O_{name}", expr)


def write(b, port, reg, val):
    O(b, f"{port}i", reg)
    for k in range(4):
        O(b, f"{port}v{k}", val[k])


def regs(p):
    return [V(f"{p}{k}") for k in range(4)]


def rn_or_sp():
    return [sel(eq(V("rn"), 31), V(f"SP{k}"), V(f"A{k}")) for k in range(4)]


def pc64(lo, hi):
    return [lo, hi, "0", "0"]


def branch(b, p, off_lo, off_hi):
    """pc + offset (32-bit wrap) into the npc outputs."""
    r = alu.add32(b, p, [S("pc0"), S("pc1")], [off_lo, off_hi])
    O(b, "npc0", r[0])
    O(b, "npc1", r[1])


def rd_target(spok):
    """Destination register number: rd, with 31 meaning SP (spok=1) or nothing (spok=0)."""
    return sel(eq(V("rd"), 31), sel(spok, SP_CUR, NOREG), V("rd"))


def cls_addsub_common(b):
    """Inputs: ADa (a), ADb (operand already inverted for subtraction), ADcin, ADS, ADsp."""
    a, c = regs("ADa"), regs("ADb")
    res, n, z, cf, v = alu.add(b, "AD", a, c, V("ADcin"), V("sf"))
    write(b, "w1", rd_target(f"(1 - {V('ADS')}) * {V('ADsp')}"), res)
    O(b, "nzcv", sel(V("ADS"), alu.nzcv(n, z, cf, v), S("nzcv")))


def cls_addsub_minor(minor):
    b = Block()
    sf, op = V("sf"), V("b30")
    if minor == "ADDSUB_IMM":
        imm, sh = V("imm12"), V("b22")
        raw = [sel(sh, f"mod({imm}, 16) * 4096", imm), sel(sh, floordiv(imm, 16), 0), "0", "0"]
        a, sp, cin = rn_or_sp(), 1, op
    elif minor == "ADDSUB_SREG":
        raw = alu.shift(b, "SH", regs("B"), V("opc"), f"mod({V('imm6')}, {sel(sf, 64, 32)})", sf)
        a, sp, cin = regs("A"), 0, op
    elif minor == "ADDSUB_EREG":
        raw = extend_shift(b, "EX", regs("B"), V("opt"), V("imm3"))
        a, sp, cin = rn_or_sp(), 1, op
    else:  # ADC / SBC
        raw = regs("B")
        a, sp, cin = regs("A"), 0, V("fC")
    rawl = let4(b, "ADraw", raw)
    let4(b, "ADa", a)
    let4(b, "ADb", [sel(op, f"({M16} - {r})", r) for r in rawl])
    b.let("ADcin", cin)
    b.let("ADS", V("b29"))
    b.let("ADsp", sp)
    return b


def extend_shift(b, p, x, option, amount):
    """Extend (UXTB..SXTX by option) then shift left by amount (0..4)."""
    sz = b.let(f"{p}sz", mod(option, 4))
    sg = b.let(f"{p}sg", bit(option, 2))
    is_ = [b.let(f"{p}is{k}", eq(sz, k)) for k in range(4)]
    sb = b.let(f"{p}sb", f"{sg} * ({is_[0]} * {bit(x[0], 7)} + {is_[1]} * {bit(x[0], 15)} + {is_[2]} * {bit(x[1], 15)})")
    fill = f"{sb} * {M16}"
    e = [
        f"{is_[0]} * (mod({x[0]}, 256) + {sb} * 65280) + (1 - {is_[0]}) * {x[0]}",
        f"max({is_[0]}, {is_[1]}) * {fill} + (1 - max({is_[0]}, {is_[1]})) * {x[1]}",
        f"(1 - {is_[3]}) * {fill} + {is_[3]} * {x[2]}",
        f"(1 - {is_[3]}) * {fill} + {is_[3]} * {x[3]}",
    ]
    e = let4(b, f"{p}e", e)
    pw = b.let(f"{p}pw", f"pow(2, {amount})")
    outv = []
    for k in range(4):
        lo = f"mod({e[k]} * {pw}, 65536)"
        carry = f"{floordiv(f'{e[k - 1]} * {pw}', 65536)}" if k else "0"
        outv.append(b.let(f"{p}o{k}", f"{lo} + {carry}"))
    return outv


def cls_logic_common(b):
    """Inputs: LGb (second operand, already inverted for BIC/ORN/EON), LGsp. op from opc2."""
    a, c, sf = regs("A"), regs("LGb"), V("sf")
    andv = alu.band(b, "LGand", a, c)
    opc = V("opc2")
    r = []
    for k in range(4):
        orv = f"{a[k]} + {c[k]} - {andv[k]}"
        xorv = f"{a[k]} + {c[k]} - 2 * {andv[k]}"
        e = f"{eq(opc, 1)} * ({orv}) + {eq(opc, 2)} * ({xorv}) + (1 - {eq(opc, 1)} - {eq(opc, 2)}) * {andv[k]}"
        r.append(b.let(f"LGr{k}", e if k < 2 else f"{sf} * ({e})"))
    flags = b.let("LGs", eq(opc, 3))
    n = bit(sel(sf, r[3], r[1]), 15)
    z = alu.is_zero(r, sf)
    write(b, "w1", rd_target(f"(1 - {flags}) * {V('LGsp')}"), r)
    O(b, "nzcv", sel(flags, f"{n} * 8 + ({z}) * 4", S("nzcv")))


def cls_logic_minor(minor):
    b = Block()
    sf = V("sf")
    if minor == "LOGIC_IMM":
        imm = logic_imm(b, "LI", V("b22"), V("imm6"), V("immr"), sf)
        let4(b, "LGb", imm)
        b.let("LGsp", 1)
    else:
        sh = alu.shift(b, "SH", regs("B"), V("opc"), f"mod({V('imm6')}, {sel(sf, 64, 32)})", sf)
        nb = V("b21")
        let4(b, "LGb", [sel(nb, f"({M16} - {s})" if k < 2 else f"{sf} * ({M16} - {s})", s) for k, s in enumerate(sh)])
        b.let("LGsp", 0)
    return b


def bitmask_parts(b, p, n, imms, immr):
    """DecodeBitMasks: element size E from N:NOT(imms), S and R within the element, and a flag
    that's 0 for the reserved encodings (which give all-zero masks, as in the ISS)."""
    v = b.let(f"{p}v", f"{n} * 64 + 63 - {imms}")
    ln = b.let(f"{p}len", " + ".join(ge(v, 1 << k) for k in range(1, 7)))
    es = b.let(f"{p}E", f"pow(2, {ln})")
    s = b.let(f"{p}S", f"mod({imms}, {es})")
    r = b.let(f"{p}R", f"mod({immr}, {es})")
    ok = b.let(f"{p}ok", ge(v, 2))
    return es, s, r, ok


def logic_imm(b, p, n, imms, immr, sf):
    """Logical immediate: bit j is set when (j + R) mod E <= S."""
    es, s, r, ok = bitmask_parts(b, p, n, imms, immr)
    limbs_ = []
    for k in range(4):
        terms = " + ".join(f"clamp(0, {s} - mod({16 * k + i} + {r}, {es}) + 1, 1) * {1 << i}" for i in range(16))
        limbs_.append(b.let(f"{p}{k}", f"{ok} * ({terms})" if k < 2 else f"{sf} * {ok} * ({terms})"))
    return limbs_


def cls_movw():
    b = Block()
    hw, imm, opc, sf = V("hw"), V("imm16"), V("opc2"), V("sf")
    r = []
    for k in range(4):
        hit = eq(hw, k)
        movn = f"{M16} - {hit} * {imm}"
        movz = f"{hit} * {imm}"
        movk = sel(hit, imm, V(f"T{k}"))
        e = f"{eq(opc, 0)} * ({movn}) + {eq(opc, 2)} * ({movz}) + {eq(opc, 3)} * ({movk})"
        r.append(b.let(f"MWr{k}", e if k < 2 else f"{sf} * ({e})"))
    write(b, "w1", rd_target(0), r)
    return b


def cls_bitfield():
    """SBFM/BFM/UBFM, bit by bit from wmask and tmask as in the ARM pseudocode."""
    b = Block()
    sf, immr, imms, opc = V("sf"), V("immr"), V("imm6"), V("opc2")
    w = b.let("BFw", sel(sf, 64, 32))
    es, s, r, ok = bitmask_parts(b, "BFk", V("b22"), imms, immr)
    d = b.let("BFd", f"mod({s} - {r}, {es})")
    src = alu.mask_sf(b, "BFs", regs("A"), sf)
    rot = alu.shift(b, "BFr", src, 3, f"mod({immr}, {w})", sf)
    dst = [f"{eq(opc, 1)} * {x}" for x in regs("T")]
    dst = alu.mask_sf(b, "BFt", dst, sf)
    sgn = b.let("BFsg", f"mod(round(down, {onehot_limb(src, floordiv(imms, 16))} / pow(2, mod({imms}, 16))), 2)")
    is0, is2 = eq(opc, 0), eq(opc, 2)
    out_ = []
    for k in range(4):
        terms = []
        for i in range(16):
            j = 16 * k + i
            wm = b.let(f"BFwm{j}", f"{ok} * clamp(0, {s} - mod({j} + {r}, {es}) + 1, 1)")
            tm = b.let(f"BFtm{j}", f"{ok} * clamp(0, {d} - mod({j}, {es}) + 1, 1)")
            bot = sel(wm, bit(rot[k], i), bit(dst[k], i))
            top = f"({is0} * {sgn} + (1 - {is0} - {is2}) * {bit(dst[k], i)})"
            terms.append(f"{sel(tm, bot, top)} * {1 << i}")
        out_.append(b.let(f"BFo{k}", " + ".join(terms)))
    write(b, "w1", rd_target(0), [out_[0], out_[1], f"{sf} * {out_[2]}", f"{sf} * {out_[3]}"])
    return b


def onehot_limb(x, k):
    return "(" + " + ".join(f"{eq(k, i)} * {x[i]}" for i in range(4)) + ")"


def cls_extr():
    b = Block()
    sf = V("sf")
    n, m = regs("A"), regs("B")
    lo = [m[0], m[1], sel(sf, m[2], n[0]), sel(sf, m[3], n[1])]
    hi = [sel(sf, x, "0") for x in n]
    r = alu.funnel(b, "EXF", let4(b, "EXlo", lo), let4(b, "EXhi", hi), V("imm6"))
    write(b, "w1", rd_target(0), [r[0], r[1], f"{sf} * {r[2]}", f"{sf} * {r[3]}"])
    return b


def cls_adr():
    b = Block()
    op = V("sf")
    # The signed 21-bit immediate, kept inline: Chrome rounds a property of a million or more
    # (evaluate.Stage.run), and this can reach 2^20.
    s19 = b.let("ADRs", f"{V('imm19')} - {bit(V('imm19'), 18)} * 524288")
    sv = f"({s19} * 4 + {V('opc2')})"
    fill = b.let("ADRf", f"{lt(s19, 0)} * {M16}")
    off = [b.let("ADRo0", mod(sv, 65536)), b.let("ADRo1", mod(floordiv(sv, 65536), 65536)), fill, fill]
    # ADRP: the offset is imm21 << 12, built from the signed value directly
    w = b.let("ADRw", floordiv(sv, 16))
    offp = [b.let("ADRp0", f"mod({sv}, 16) * 4096"), b.let("ADRp1", mod(w, 65536)),
            b.let("ADRp2", mod(floordiv(w, 65536), 65536)), b.let("ADRp3", mod(floordiv(w, 65536), 65536))]
    base = [sel(op, f"{S('pc0')} - mod({S('pc0')}, 4096)", S("pc0")), S("pc1"), "0", "0"]
    c = [sel(op, offp[k], off[k]) for k in range(4)]
    res, *_ = alu.add(b, "ADRa", let4(b, "ADRb", base), let4(b, "ADRc", c), 0, 1)
    write(b, "w1", rd_target(0), res)
    return b


def cls_b():
    b = Block()
    I0, I1 = V("I0"), V("I1")
    o0 = b.let("Bo0", mod(f"{I0} * 4", 65536))
    hs = b.let("Bhs", f"mod({I1}, 1024) - {ge(mod(I1, 1024), 512)} * 1024")
    o1 = b.let("Bo1", mod(f"{hs} * 4 + {floordiv(f'{I0} * 4', 65536)}", 65536))
    branch(b, "Bt", o0, o1)
    link = V("sf")
    O(b, "w1i", sel(link, 30, NOREG))
    for k, v in enumerate(pc64(V("pn0"), V("pn1"))):
        O(b, f"w1v{k}", v)
    return b


def imm_branch(b, p, imm, bits, taken):
    """npc = taken ? pc + sext(imm)*4 : pc + 4, for imm of `bits` bits (bits <= 19)."""
    sv = b.let(f"{p}sv", f"{imm} - {bit(imm, bits - 1)} * {1 << bits}")
    o = f"({sv} * 4)"   # inline: Chrome rounds a property of a million or more (evaluate.Stage.run)
    lo = b.let(f"{p}l", mod(o, 65536))
    hi = b.let(f"{p}h", mod(floordiv(o, 65536), 65536))
    r = alu.add32(b, f"{p}t", [S("pc0"), S("pc1")], [lo, hi])
    O(b, "npc0", sel(taken, r[0], V("pn0")))
    O(b, "npc1", sel(taken, r[1], V("pn1")))


def cls_bcond():
    b = Block()
    c = alu.cond(V("c0"), V("fN"), V("fZ"), V("fC"), V("fV"))
    t = b.let("BCt", c)
    imm_branch(b, "BC", V("imm19"), 19, t)
    return b


def cls_cb():
    b = Block()
    z = alu.is_zero(regs("T"), V("sf"))
    t = b.let("CBt", f"{eq(z, 1)} * (1 - {V('b24')}) + (1 - {z}) * {V('b24')}")
    imm_branch(b, "CB", V("imm19"), 19, t)
    return b


def cls_tb():
    b = Block()
    bn = b.let("TBn", f"{V('sf')} * 32 + {field(V('I1'), 3, 5)}")
    limb = onehot_limb(regs("T"), floordiv(bn, 16))
    bv = b.let("TBv", f"mod(round(down, {limb} / pow(2, mod({bn}, 16))), 2)")
    t = b.let("TBt", eq(bv, V("b24")))
    imm_branch(b, "TB", V("imm14"), 14, t)
    return b


def cls_br():
    b = Block()
    opc = b.let("BRop", field(V("I1"), 5, 4))
    eret = b.let("BRe", eq(opc, 4))
    O(b, "npc0", sel(eret, S("elr0"), V("A0")))
    O(b, "npc1", sel(eret, S("elr1"), V("A1")))
    O(b, "w1i", sel(eq(opc, 1), 30, NOREG))
    for k, v in enumerate(pc64(V("pn0"), V("pn1"))):
        O(b, f"w1v{k}", v)
    sp0, sp1 = S("spsr0"), S("spsr1")
    O(b, "nzcv", sel(eret, floordiv(sp1, 4096), S("nzcv")))
    O(b, "daif", sel(eret, field(sp0, 6, 4), S("daif")))
    O(b, "el", sel(eret, bit(sp0, 2), S("el")))
    O(b, "spsel", sel(eret, bit(sp0, 0), S("spsel")))
    return b


def cls_exc_common(b):
    """Inputs: EXr0/EXr1 (return address), EXirq. Enters the EL1 exception vector."""
    m = sel(S("el"), sel(S("spsel"), 5, 4), 0)
    O(b, "spsr0", f"{S('daif')} * 64 + {m}")
    O(b, "spsr1", f"{S('nzcv')} * 4096")
    O(b, "elr0", V("EXr0"))
    O(b, "elr1", V("EXr1"))
    O(b, "el", 1)
    O(b, "spsel", 1)
    O(b, "daif", 15)
    off = b.let("EXoff", f"{sel(S('el'), sel(S('spsel'), 512, 0), 1024)} + {V('EXirq')} * 128")
    r = alu.add32(b, "EXv", [S("vbar0"), S("vbar1")], [off, "0"])
    O(b, "npc0", r[0])
    O(b, "npc1", r[1])


def cls_exc_minor(minor):
    b = Block()
    if minor == "SVC":
        b.let("EXr0", V("pn0"))
        b.let("EXr1", V("pn1"))
        O(b, "esr0", V("imm16"))
        O(b, "esr1", M.ESR_SVC >> 16)
        b.let("EXirq", 0)
    else:
        b.let("EXr0", S("pc0"))
        b.let("EXr1", S("pc1"))
        b.let("EXirq", 1 if minor == "IRQ" else 0)
        if minor == "UNDEF":
            O(b, "esr0", 0)
            O(b, "esr1", M.ESR_UNDEF >> 16)
    return b


SYSREG_LIST = list(M.SYSREGS.items())


def cls_sys():
    b = Block()
    I0, I1 = V("I0"), V("I1")
    op0 = b.let("SYo0", field(I1, 3, 2))
    isreg = b.let("SYreg", ge(op0, 2))
    load = V("b21")
    key = b.let("SYk", f"{field(I0, 5, 11)} + {mod(I1, 16)} * 2048")
    hit = {name: b.let(f"SYh{name}", f"{isreg} * {eq(key, k)}") for k, name in SYSREG_LIST}
    mrs, msr = b.let("SYmrs", f"{isreg} * {load}"), b.let("SYmsr", f"{isreg} * (1 - {load})")
    T = regs("T")
    # MRS: read value (limbs 0,1 plus SP_EL0's upper limbs)
    vals = {
        "NZCV": ["0", f"{S('nzcv')} * 4096"], "DAIF": [f"{S('daif')} * 64", "0"],
        "CurrentEL": [f"{S('el')} * 4", "0"], "SPSel": [S("spsel"), "0"],
        "ELR_EL1": [S("elr0"), S("elr1")], "SPSR_EL1": [S("spsr0"), S("spsr1")],
        "SP_EL0": [S("sp0_0"), S("sp0_1")], "ESR_EL1": [S("esr0"), S("esr1")],
        "VBAR_EL1": [S("vbar0"), S("vbar1")], "CNTP_TVAL_EL0": [S("tval0"), S("tval1")],
        "CNTP_CTL_EL0": [f"mod({S('tctl')}, 4) + {V('tist')} * 4", "0"], "CNTPCT_EL0": [S("cnt0"), S("cnt1")],
        "ICC_PMR_EL1": [S("pmr"), "0"],
        "ICC_IAR1_EL1": [sel(V("gpend"), sel(V("tasrt"), M.TIMER_INTID, M.KBD_INTID), M.SPURIOUS_INTID), "0"],
        "ICC_EOIR1_EL1": ["0", "0"],
        "ICC_RPR_EL1": [sel(S("gact"), M.TIMER_PRIO, M.IDLE_PRIO), "0"],
    }
    rv = [" + ".join(f"{hit[n]} * ({vals[n][k]})" for n in hit) for k in range(2)]
    rv += [f"{hit['SP_EL0']} * {S('sp0_2')}", f"{hit['SP_EL0']} * {S('sp0_3')}"]
    O(b, "w1i", sel(mrs, rd_target(0), NOREG))
    for k in range(4):
        O(b, f"w1v{k}", rv[k])
    # MSR (register)
    def m(name):
        return f"{msr} * {hit[name]}"
    # MSR (immediate): SPSel, DAIFSet, DAIFClr
    pst = b.let("SYps", f"{eq(op0, 0)} * {eq(field(I0, 12, 4), 4)}")
    op1, op2, crm = mod(I1, 8), field(I0, 5, 3), field(I0, 8, 4)
    isp = b.let("SYisp", f"{pst} * {eq(op1, 0)} * {eq(op2, 5)}")
    iset = b.let("SYiset", f"{pst} * {eq(op1, 3)} * {eq(op2, 6)}")
    iclr = b.let("SYiclr", f"{pst} * {eq(op1, 3)} * {eq(op2, 7)}")
    daif = S("daif")
    dset = " + ".join(f"max({bit(daif, i)}, {bit(crm, i)}) * {1 << i}" for i in range(4))
    dclr = " + ".join(f"{bit(daif, i)} * (1 - {bit(crm, i)}) * {1 << i}" for i in range(4))
    O(b, "nzcv", sel(m("NZCV"), floordiv(T[1], 4096), S("nzcv")))
    O(b, "daif", sel(m("DAIF"), field(T[0], 6, 4), sel(iset, dset, sel(iclr, dclr, daif))))
    O(b, "spsel", sel(m("SPSel"), mod(T[0], 2), sel(isp, mod(crm, 2), S("spsel"))))
    for nm, o in (("ELR_EL1", "elr"), ("SPSR_EL1", "spsr"), ("ESR_EL1", "esr"), ("VBAR_EL1", "vbar")):
        O(b, f"{o}0", sel(m(nm), T[0], S(f"{o}0")))
        O(b, f"{o}1", sel(m(nm), T[1], S(f"{o}1")))
    O(b, "w2i", sel(m("SP_EL0"), SP_EL0, NOREG))
    for k in range(4):
        O(b, f"w2v{k}", T[k])
    O(b, "tvw", m("CNTP_TVAL_EL0"))
    O(b, "tv0", T[0])
    O(b, "tv1", T[1])
    O(b, "tctl", sel(m("CNTP_CTL_EL0"), mod(T[0], 4), S("tctl")))
    O(b, "pmr", sel(m("ICC_PMR_EL1"), f"{floordiv(mod(T[0], 256), 8)} * 8", S("pmr")))
    # Reading IAR1 acknowledges the pending interrupt; writing EOIR1 ends it.
    ack = f"{mrs} * {hit['ICC_IAR1_EL1']} * {V('gpend')}"
    O(b, "gact", sel(ack, 1, sel(m("ICC_EOIR1_EL1"), 0, S("gact"))))
    return b


def cls_csel():
    b = Block()
    sf = V("sf")
    t = b.let("CSt", alu.cond(V("c12"), V("fN"), V("fZ"), V("fC"), V("fV")))
    inv = V("b30")
    m = let4(b, "CSm", [sel(inv, f"({M16} - {x})", x) for x in regs("B")])
    inc, *_ = alu.add(b, "CSa", m, ["0"] * 4, V("b10"), 1)
    r = [sel(t, V(f"A{k}"), inc[k]) for k in range(4)]
    write(b, "w1", rd_target(0), [r[0], r[1], f"{sf} * {r[2]}", f"{sf} * {r[3]}"])
    return b


def cls_ccmp():
    b = Block()
    sf, op = V("sf"), V("b30")
    t = b.let("CCt", alu.cond(V("c12"), V("fN"), V("fZ"), V("fC"), V("fV")))
    immf = V("b11")
    src = [sel(immf, V("rm"), V("B0"))] + [sel(immf, "0", V(f"B{k}")) for k in (1, 2, 3)]
    c = let4(b, "CCb", [sel(op, f"({M16} - {x})", x) for x in src])
    _, n, z, cf, v = alu.add(b, "CCa", regs("A"), c, op, sf)
    O(b, "nzcv", sel(t, alu.nzcv(n, z, cf, v), V("c0")))
    return b


def cls_dp2():
    b = Block()
    sf, opc = V("sf"), V("imm6")
    w = b.let("D2w", sel(sf, 64, 32))
    sh = alu.shift(b, "D2s", regs("A"), f"{opc} - 8", f"mod({V('B0')}, {w})", sf)
    q = divide(b, "DV", sf, eq(opc, 3))
    isdiv = b.let("D2d", f"{eq(opc, 2)} + {eq(opc, 3)}")
    write(b, "w1", rd_target(0), [sel(isdiv, q[k], sh[k]) for k in range(4)])
    return b


def neg(b, p, x, sf):
    r, *_ = alu.add(b, p, [f"({M16} - {l})" for l in x], ["0"] * 4, 1, sf)
    return r


def divide(b, p, sf, signed):
    """Restoring long division, 64 iterations; division by zero gives 0 (UDIV/SDIV)."""
    a = alu.mask_sf(b, f"{p}a", regs("A"), sf)
    d = alu.mask_sf(b, f"{p}d", regs("B"), sf)
    sa = b.let(f"{p}sa", f"{signed} * {bit(sel(sf, a[3], a[1]), 15)}")
    sd = b.let(f"{p}sd", f"{signed} * {bit(sel(sf, d[3], d[1]), 15)}")
    na, nd = neg(b, f"{p}na", a, sf), neg(b, f"{p}nd", d, sf)
    n = let4(b, f"{p}n", [sel(sa, na[k], a[k]) for k in range(4)])
    dv = let4(b, f"{p}v", [sel(sd, nd[k], d[k]) for k in range(4)])
    R = ["0", "0", "0", "0"]
    qbits = []
    for i in range(63, -1, -1):
        pi = f"{p}{i}_"
        nb = bit(n[i // 16], i % 16)
        t, carry = [], nb
        for k in range(4):
            s = b.let(f"{pi}s{k}", f"2 * {R[k]} + {carry}")
            t.append(b.let(f"{pi}t{k}", mod(s, 65536)))
            carry = floordiv(s, 65536)
        e = b.let(f"{pi}e", carry)
        u, borrow = [], "0"
        for k in range(4):
            s = b.let(f"{pi}u{k}", f"{t[k]} - {dv[k]} - {borrow}")
            u.append(mod(s, 65536))
            borrow = b.let(f"{pi}b{k}", f"0 - {floordiv(s, 65536)}")
        g = b.let(f"{pi}g", f"max({e}, 1 - {borrow})")
        R = [b.let(f"{pi}R{k}", sel(g, u[k], t[k])) for k in range(4)]
        qbits.append((i, g))
    qb = dict(qbits)
    q = let4(b, f"{p}q", [" + ".join(f"{qb[16 * k + j]} * {1 << j}" for j in range(16)) for k in range(4)])
    nq = neg(b, f"{p}nq", q, sf)
    flip = b.let(f"{p}f", f"{ne(sa, sd)}")
    dz = b.let(f"{p}z", alu.is_zero(dv, 1))
    res = [f"(1 - {dz}) * {sel(flip, nq[k], q[k])}" for k in range(4)]
    return let4(b, f"{p}r", [res[0], res[1], f"{sf} * {res[2]}", f"{sf} * {res[3]}"])


def cls_dp1():
    b = Block()
    sf, opc = V("sf"), V("imm6")
    a = alu.mask_sf(b, "D1a", regs("A"), sf)
    w = sel(sf, 64, 32)
    bits_ = [b.let(f"D1b{i}", bit(a[i // 16], i % 16)) for i in range(64)]
    # RBIT: bit i of the result is bit (w-1-i)
    rb = []
    for k in range(4):
        terms = [f"{sel(sf, bits_[63 - (16 * k + j)], bits_[31 - (16 * k + j)] if 16 * k + j < 32 else '0')} * {1 << j}"
                 for j in range(16)]
        rb.append(" + ".join(terms))
    sw = [f"mod({a[k]}, 256) * 256 + {floordiv(a[k], 256)}" for k in range(4)]
    rev16 = sw
    rev32 = [sw[1], sw[0], sw[3], sw[2]]
    rev64 = [sw[3], sw[2], sw[1], sw[0]]
    # CLZ over w bits: count of leading zero bits, via running "all zero so far" products
    z = "1"
    zs = []
    for i in range(63, -1, -1):
        z = b.let(f"D1z{i}", f"{z} * (1 - {bits_[i]})")
        zs.append((i, z))
    clz64 = " + ".join(zz for i, zz in zs)
    clz32 = " + ".join(zz for i, zz in zs if i < 32)
    # CLS: CLZ of (x XOR x<<1) over w-1 bits
    xs = []
    x = "1"
    for i in range(62, -1, -1):
        same = eq(bits_[i + 1], bits_[i])
        # In 32-bit mode the comparison starts below bit 31.
        x = b.let(f"D1x{i}", f"{x} * {sel(sf, same, 1) if i >= 31 else same}")
        xs.append((i, x))
    cls64 = " + ".join(xx for i, xx in xs)
    cls32 = " + ".join(xx for i, xx in xs if i < 31)
    is_ = [b.let(f"D1o{k}", eq(opc, k)) for k in range(6)]
    r = []
    for k in range(4):
        clz = sel(sf, f"({clz64})", f"({clz32})")
        cls_ = sel(sf, f"({cls64})", f"({cls32})")
        rev = sel(sf, rev64[k], rev32[k])
        e = (f"{is_[0]} * ({rb[k]}) + {is_[1]} * ({rev16[k]}) + {is_[2]} * ({rev32[k]}) + {is_[3]} * ({rev}) "
             + (f"+ {is_[4]} * {clz} + {is_[5]} * {cls_}" if k == 0 else ""))
        r.append(e)
    write(b, "w1", rd_target(0), [r[0], r[1], f"{sf} * ({r[2]})", f"{sf} * ({r[3]})"])
    return b


def cls_dp3():
    b = Block()
    sf = V("sf")
    op31 = b.let("D3op", field(V("I1"), 5, 3))
    o0 = V("b15")
    A, B, Ra = regs("A"), regs("B"), regs("U")
    sgn_l = b.let("D3sl", eq(op31, 1))
    wide = b.let("D3wd", f"{eq(op31, 1)} + {eq(op31, 5)}")
    # operands: 64-bit, or 32-bit sign/zero extended for the long forms
    fa = f"{sgn_l} * {bit(A[1], 15)} * {M16}"
    fb = f"{sgn_l} * {bit(B[1], 15)} * {M16}"
    a = let4(b, "D3a", [A[0], A[1], sel(wide, fa, A[2]), sel(wide, fa, A[3])])
    c = let4(b, "D3b", [B[0], B[1], sel(wide, fb, B[2]), sel(wide, fb, B[3])])
    ab = [b.let(f"D3ab{i}", f"mod({floordiv(a[i // 2], 256)}, 256)" if i % 2 else f"mod({a[i // 2]}, 256)") for i in range(8)]
    bb = [b.let(f"D3bb{i}", f"mod({floordiv(c[i // 2], 256)}, 256)" if i % 2 else f"mod({c[i // 2]}, 256)") for i in range(8)]
    carry = "0"
    pbytes = []
    for pcol in range(16):
        terms = [f"{ab[i]} * {bb[pcol - i]}" for i in range(8) if 0 <= pcol - i < 8]
        t = b.let(f"D3t{pcol}", " + ".join(terms) + f" + {carry}")
        pbytes.append(b.let(f"D3y{pcol}", mod(t, 256)))
        carry = floordiv(t, 256)
    lo = let4(b, "D3lo", [f"{pbytes[2 * k]} + {pbytes[2 * k + 1]} * 256" for k in range(4)])
    hi = let4(b, "D3hi", [f"{pbytes[8 + 2 * k]} + {pbytes[9 + 2 * k]} * 256" for k in range(4)])
    # SMULH: unsigned high part minus the sign corrections
    sa, sb = b.let("D3sa", bit(A[3], 15)), b.let("D3sb", bit(B[3], 15))
    c1 = let4(b, "D3c1", [f"{M16} - {sa} * {B[k]}" for k in range(4)])
    h1, *_ = alu.add(b, "D3h1", hi, c1, 1, 1)
    c2 = let4(b, "D3c2", [f"{M16} - {sb} * {A[k]}" for k in range(4)])
    h2, *_ = alu.add(b, "D3h2", let4(b, "D3h1x", h1), c2, 1, 1)
    # MADD/MSUB: Ra +/- product
    pm = let4(b, "D3pm", [sel(o0, f"({M16} - {lo[k]})", lo[k]) for k in range(4)])
    acc, *_ = alu.add(b, "D3ac", Ra, pm, o0, 1)
    smulh, umulh = eq(op31, 2), eq(op31, 6)
    r = [f"{smulh} * {h2[k]} + {umulh} * {hi[k]} + (1 - {smulh} - {umulh}) * {acc[k]}" for k in range(4)]
    write(b, "w1", rd_target(0), [r[0], r[1], f"{sf} * ({r[2]})", f"{sf} * ({r[3]})"])
    return b


# --- loads and stores ---------------------------------------------------------------------------
def cls_mem_minor(minor):
    """Each form sets the address (MEMa), writeback, access size and kind, then mem_common does the rest."""
    b = Block()
    sz, opc = V("size"), V("opc")
    base = rn_or_sp()
    wb, nop, pair = 0, 0, 0
    if minor == "LDST_UIMM":
        o = b.let("MUo", f"{V('imm12')} * pow(2, {sz})")
        off = [mod(o, 65536), floordiv(o, 65536), "0", "0"]
        addr = mem_add(b, base, off)
        kind, lsz = opc, sz
        nop = f"{eq(opc, 2)} * {eq(sz, 3)}"
    elif minor == "LDST_IMM9":
        off = alu.sext_small(b, "MI", V("imm9"), 9)
        moved = mem_add(b, base, off)
        post = eq(V("mode"), 1)
        wb = b.let("MEMwbf", f"{eq(V('mode'), 1)} + {eq(V('mode'), 3)}")
        addr = [sel(post, base[k], moved[k]) for k in range(4)]
        let4(b, "MEMwv", moved)
        kind, lsz = opc, sz
        nop = f"{eq(opc, 2)} * {eq(sz, 3)}"
    elif minor == "LDST_REG":
        amt = f"{V('b12')} * {sz}"
        off = extend_shift(b, "EX", regs("B"), V("opt"), amt)
        addr = mem_add(b, base, off)
        kind, lsz = opc, sz
        nop = f"{eq(opc, 2)} * {eq(sz, 3)}"
    elif minor == "LDR_LIT":
        sv = b.let("MLsv", f"{V('imm19')} - {bit(V('imm19'), 18)} * 524288")
        o = f"({sv} * 4)"   # inline: Chrome rounds a property of a million or more (evaluate.Stage.run)
        fill = f"{lt(sv, 0)} * {M16}"
        off = [mod(o, 65536), mod(floordiv(o, 65536), 65536), fill, fill]
        addr = mem_add(b, [S("pc0"), S("pc1"), "0", "0"], off)
        so = V("size")  # insn[31:30] picks the form here
        kind = sel(eq(so, 2), 2, 1)
        lsz = sel(eq(so, 1), 3, 2)
        nop = eq(so, 3)
    else:  # LDSTP
        so = V("size")
        lsz = b.let("MPsz", sel(eq(so, 2), 3, 2))
        sv = b.let("MPsv", f"{V('imm7')} - {bit(V('imm7'), 6)} * 128")
        o = b.let("MPo", f"{sv} * pow(2, {lsz})")
        fill = f"{lt(o, 0)} * {M16}"
        off = [mod(o, 65536), mod(floordiv(o, 65536), 65536), fill, fill]
        moved = mem_add(b, base, off)
        md = field(V("I1"), 7, 2)  # insn[24:23]
        wb = b.let("MEMwbf", f"{eq(md, 1)} + {eq(md, 3)}")
        addr = [sel(eq(md, 1), base[k], moved[k]) for k in range(4)]
        let4(b, "MEMwv", moved)
        kind = sel(V("b22"), sel(eq(so, 1), 2, 1), 0)
        pair = 1
    let4(b, "MEMa", addr)
    if not wb:
        let4(b, "MEMwv", ["0"] * 4)
    b.let("MEMwb", wb)
    b.let("MEMnop", nop)
    b.let("MEMpair", pair)
    b.let("MEMk", kind)
    b.let("MEMsz", lsz)
    return b


def mem_add(b, base, off):
    n = len(b.decls)
    r, *_ = alu.add(b, f"MA{n}_", base, off, 0, 1)
    return r


def mem_common(b):
    a = regs("MEMa")
    kind, nb_l, pair, nop = V("MEMk"), V("MEMsz"), V("MEMpair"), V("MEMnop")
    nb = b.let("MEMnb", f"pow(2, {nb_l})")
    low = b.let("MEMlow", f"{eq(a[1], 0)} * {eq(a[2], 0)} * {eq(a[3], 0)}")
    isrx = b.let("MEMrx", f"{low} * {eq(a[0], M.MMIO_RX)}")
    istx = b.let("MEMtx", f"{low} * {eq(a[0], M.MMIO_TX)}")
    o = b.let("MEMo", mod(a[0], 4))
    store = b.let("MEMst", f"{eq(kind, 0)} * (1 - {nop})")
    load = b.let("MEMld", f"{ge(kind, 1)} * (1 - {nop})")
    total = b.let("MEMtot", f"{nb} * (1 + {pair})")
    cell = b.let("MEMc", floordiv(a[0], 4))
    # The cache holds ROM and RAM, which sit next to each other from address 0.
    cached = b.let("MEMcch", f"{low} * {lt(a[0], M.RAM_BASE + M.RAM_SIZE)}")
    inram = b.let("MEMram", f"{cached} * {ge(a[0], M.RAM_BASE)}")
    seen, cw = cache_lookup(b, cell, floordiv(f"{o} + {total} - 1", 4))
    first = iz(S("phase"))
    hit = b.let("MEMhit", f"{load} * {first} * {cached} * {seen}")
    O(b, "hit", hit)
    # The cache is write-back: a store to RAM that one line holds all of only updates the cache.
    shit = b.let("MEMsh", f"{store} * {inram} * {seen}")
    # The bus takes one access a frame (it is a one-entry store buffer, drained next frame): a
    # slot that needs it after an earlier slot used it waits for the next frame.
    blk = b.let("MEMblk", f"{V('bz')} * {low} * ({store} * (1 - {shit}) + {load} * {first} * (1 - {hit}))")
    O(b, "blk", blk)
    store = b.let("MEMstb", f"{store} * (1 - {blk})")
    sbus = b.let("MEMsbus", f"{store} * (1 - {shit})")
    # A load that misses the cache takes three frames: phase 0 puts the address on the bus,
    # phase 1 stalls while memory places its readers, and phase 2 reads them, fills a line and,
    # if that line held stores, writes them back over the now free bus.
    p1 = eq(S("phase"), 2)
    done = b.let("MEMdone", f"{load} * ({p1} + {hit})")
    wait = b.let("MEMwait", f"{load} * {first} * (1 - {hit}) * (1 - {blk})")
    fill = b.let("MEMfill", f"{load} * {p1} * {cached}")
    victim = [eq(S("dlr"), L) for L in range(M.DCACHE_LINES)]
    evict = b.let("MEMev", f"{fill} * (" + " + ".join(f"{victim[L]} * {S(f'dl{L}d')}" for L in range(M.DCACHE_LINES)) + ")")
    vcell = " + ".join(f"{victim[L]} * {S(f'dl{L}t')}" for L in range(M.DCACHE_LINES)) + " - 1"
    O(b, "dcell", sel(evict, vcell, sel(f"{low} * max({sbus}, {wait})", cell, -10)))
    O(b, "phase", wait)
    O(b, "npc0", sel(f"max({wait}, {blk})", S("pc0"), V("pn0")))
    O(b, "npc1", sel(f"max({wait}, {blk})", S("pc1"), V("pn1")))
    O(b, "rxrd", f"{done} * {isrx}")
    O(b, "tx", sel(f"{store} * {istx}", mod(V("T0"), 256), -1))
    O(b, "st", f"max({sbus}, {evict})")
    # --- load: window bytes [o + extra, ...) of the four cells from the address: the cache's on
    # a hit, else memory's with any words the cache holds patched in (they may be newer).
    fresh = cache_window(b)
    W = []
    for k in range(4):
        W += [sel(hit, cw[k][0], fresh[k][0]), sel(hit, cw[k][1], fresh[k][1])]
    W = let4(b, "MEMW", [sel(isrx, V("S_kbd"), W[0])] + [sel(isrx, 0, w) for w in W[1:4]]) + \
        let4(b, "MEMX", [sel(isrx, 0, w) for w in W[4:]])
    vals = []
    for which, extra in (("v1", "0"), ("v2", nb)):
        t = b.let(f"MEM{which}t", f"{o} + {extra}")
        q = b.let(f"MEM{which}q", floordiv(t, 2))
        odd = b.let(f"MEM{which}d", mod(t, 2))
        cat = W + ["0"] * 6
        L = [b.let(f"MEM{which}L{j}", " + ".join(f"{eq(q, qq)} * {cat[j + qq]}" for qq in range(6)))
             for j in range(5)]
        x = [b.let(f"MEM{which}x{j}", sel(odd, f"{floordiv(L[j], 256)} + mod({L[j + 1]}, 256) * 256", L[j]))
             for j in range(4)]
        m = alu.lowmask(b, f"MEM{which}m", x, f"8 * {nb}")
        sb = b.let(f"MEM{which}s", " + ".join(
            f"{eq(nb_l, i)} * {bit(m[(8 << i) // 16 - 1 if i else 0], 7 if i == 0 else 15)}" for i in range(4)))
        ones = alu.ones(b, f"MEM{which}o", f"8 * {nb}")
        sx = [f"{m[k]} + ({eq(kind, 2)} + {eq(kind, 3)}) * {sb} * ({M16} - {ones[k]})" for k in range(4)]
        vals.append(let4(b, f"MEM{which}r", [sx[0], sx[1], f"(1 - {eq(kind, 3)}) * ({sx[2]})",
                                              f"(1 - {eq(kind, 3)}) * ({sx[3]})"]))
    rt2 = sel(eq(V("ra"), 31), NOREG, V("ra"))
    write(b, "w1", sel(done, rd_target(0), NOREG), vals[0])
    write(b, "w2", sel(f"{done} * {pair}", rt2, NOREG), vals[1])
    wbreg = sel(eq(V("rn"), 31), SP_CUR, V("rn"))
    write(b, "w3", sel(f"{V('MEMwb')} * max({store}, {done})", wbreg, NOREG), regs("MEMwv"))
    # --- store: data bytes placed at byte o of the 16-byte window
    T, U = regs("T"), regs("U")
    tm = alu.lowmask(b, "MEMtm", T, f"8 * {nb}")
    um = alu.lowmask(b, "MEMum", U, f"8 * {nb}")
    # A pair of words fills limbs 0-3; a pair of doublewords fills 0-7. tm is already masked to nb bytes.
    is8 = eq(nb_l, 3)
    p4, p8 = f"{pair} * (1 - {is8})", f"{pair} * {is8}"
    D = [tm[0], tm[1], f"{tm[2]} + {p4} * {um[0]}", f"{tm[3]} + {p4} * {um[1]}",
         f"{p8} * {um[0]}", f"{p8} * {um[1]}", f"{p8} * {um[2]}", f"{p8} * {um[3]}"]
    D = let4(b, "MEMD", D[:4]) + let4(b, "MEMDh", D[4:])
    q, odd = b.let("MEMsq", floordiv(o, 2)), b.let("MEMsd", mod(o, 2))
    Sx = [b.let(f"MEMS{j}", sel(q, D[j - 1] if j else "0", D[j])) for j in range(8)]
    sv = [b.let(f"MEMsv{j}", sel(odd, f"mod({Sx[j]} * 256, 65536) + {floordiv(Sx[j - 1], 256) if j else '0'}",
                                 Sx[j])) for j in range(8)]
    sm = [b.let(f"MEMsm{k}", " + ".join(f"{ge(4 * k + j, o)} * {lt(4 * k + j, f'{o} + {total}')} * {1 << j}"
                                          for j in range(4))) for k in range(4)]
    # An eviction puts the whole victim line on the bus.
    for k in range(4):
        for h, half in (("0", "lo"), ("1", "hi")):
            vw = " + ".join(f"{victim[L]} * {S(f'dl{L}w{k}{h}')}" for L in range(M.DCACHE_LINES))
            O(b, f"sv{k}{half}", sel(evict, vw, sv[2 * k + int(h)]))
        O(b, f"sm{k}", sel(evict, 15, sm[k]))
    cache_update(b, cell, fill, fresh, f"{store} * {inram}", shit, sv, sm,
                 f"{load} * {first} * {cached} * (1 - {blk})")


def cache_lookup(b, cell, span):
    """Whether one cache line holds cells cell..cell+span, and its words from cell on (the
    first line that does: lines can overlap, and overlapping lines hold the same words).
    Also lets MEMq{L}_{j+3}: whether line L starts j cells after cell, for j in -3..3."""
    seen, pick = "0", []
    for L in range(M.DCACHE_LINES):
        e = b.let(f"MEMe{L}", f"{S(f'dl{L}t')} - 1 - {cell}")      # line start - cell, if <= 0
        for j in range(-3, 4):
            b.let(f"MEMq{L}_{j + 3}", eq(e, j))
        h = b.let(f"MEMh{L}", f"{nz(S(f'dl{L}t'))} * {ge(0, e)} * {ge(f'{e} + 3', span)}")
        pick.append(b.let(f"MEMp{L}", f"{h} * (1 - {seen})"))
        seen = b.let(f"MEMy{L}", f"max({seen}, {h})")
    d = b.let("MEMcd", " - ".join(["0"] + [f"{pick[L]} * {V(f'MEMe{L}')}" for L in range(M.DCACHE_LINES)]))
    lw = [[b.let(f"MEMlw{i}{h}", " + ".join(f"{pick[L]} * {S(f'dl{L}w{i}{h}')}" for L in range(M.DCACHE_LINES)))
           for h in "01"] for i in range(4)]
    cw = [[" + ".join(f"{eq(d, j)} * {lw[j + k][h]}" for j in range(4 - k)) for h in range(2)] for k in range(4)]
    return seen, cw


def at(L, j):
    """Whether cache line L starts j cells after the access's cell (see cache_lookup)."""
    return V(f"MEMq{L}_{j + 3}")


def cache_window(b):
    """The four words from the access's cell as memory showed them (D), except where a cache
    line holds the word: a dirty line's word is newer than RAM's."""
    out = []
    for i in range(4):
        # Cell i of the window is word j of line L when the line starts i - j cells after it.
        held, pick = "0", []
        for L in range(M.DCACHE_LINES):
            h = b.let(f"MEMgh{L}{i}", f"{nz(S(f'dl{L}t'))} * (" + " + ".join(at(L, i - j) for j in range(4)) + ")")
            pick.append(b.let(f"MEMgp{L}{i}", f"{h} * (1 - {held})"))
            held = b.let(f"MEMgy{L}{i}", f"max({held}, {h})")
        words = []
        for h in "01":
            terms = []
            for L in range(M.DCACHE_LINES):
                word = " + ".join(f"{at(L, i - j)} * {S(f'dl{L}w{j}{h}')}" for j in range(4))
                terms.append(f"{pick[L]} * ({word})")
            words.append(b.let(f"MEMg{i}{h}", sel(held, " + ".join(terms), V(f"D{i}{h}"))))
        out.append(words)
    return out


def cache_update(b, cell, fill, fresh, st, st_hit, sv, sm, looked):
    """A missed load fills line dlr with the words it read (clean). A store to RAM merges its
    bytes into every line holding them; when it went only to the cache, those lines are dirty."""
    for L in range(M.DCACHE_LINES):
        new = b.let(f"MEMf{L}", f"{fill} * {eq(S('dlr'), L)}")
        b.let(f"N_dl{L}t", sel(new, f"{cell} + 1", S(f"dl{L}t")))
        ms = []
        for i in range(4):
            # Word i of the line is cell e + i of the store's window.
            pos = [(k, at(L, k - i)) for k in range(4) if -3 <= k - i <= 3]
            m = b.let(f"MEMm{L}{i}", f"{st} * (" + " + ".join(f"{q} * {sm[k]}" for k, q in pos) + ")")
            ms.append(m)
            for h in "01":
                old = S(f"dl{L}w{i}{h}")
                v = " + ".join(f"{q} * {sv[2 * k + int(h)]}" for k, q in pos)
                lo_b, hi_b = (0, 1) if h == "0" else (2, 3)
                merged = (f"{old} + {st} * ({v}) - {bit(m, lo_b)} * mod({old}, 256)"
                          f" - {bit(m, hi_b)} * ({old} - mod({old}, 256))")
                b.let(f"N_dl{L}w{i}{h}", sel(new, fresh[i][int(h)], merged))
        touched = f"min(1, {' + '.join(ms)})"
        b.let(f"N_dl{L}d", sel(new, 0, f"max({S(f'dl{L}d')}, {st_hit} * {touched})"))
    b.let("N_dlr", mod(f"{S('dlr')} + {fill}", M.DCACHE_LINES))
    b.let("N_dlh", sel(looked, V("MEMy" + str(M.DCACHE_LINES - 1)), S("dlh")))


# --- assembling the execute-stage rules ---------------------------------------------------------
def stall_block():
    b = Block()
    O(b, "npc0", S("pc0"))
    O(b, "npc1", S("pc1"))
    O(b, "phase", sel(eq(S("phase"), 1), 2, S("phase")))
    # A load's wait frame keeps its address on the bus: phase 2 reads the ROM data table for it.
    O(b, "dcell", sel(eq(S("phase"), 1), f"{S('bd')} - {BUS_BIAS['bd']}", -10))
    return b


def hold_block():
    b = Block()
    O(b, "npc0", S("pc0"))
    O(b, "npc1", S("pc1"))
    O(b, "phase", S("phase"))
    return b


SINGLE = {"HOLD": hold_block, "MOVW": cls_movw, "BITFIELD": cls_bitfield, "EXTR": cls_extr, "ADR": cls_adr, "B": cls_b,
          "BCOND": cls_bcond, "CB": cls_cb, "TB": cls_tb, "SYS": cls_sys, "BR": cls_br, "CCMP": cls_ccmp,
          "CSEL": cls_csel, "DP2": cls_dp2, "DP1": cls_dp1, "DP3": cls_dp3, "STALL": stall_block}
COMMON = {"ADDSUB": cls_addsub_common, "LOGIC": cls_logic_common, "MEM": mem_common, "EXC": cls_exc_common}
MINOR = {"ADDSUB": cls_addsub_minor, "LOGIC": cls_logic_minor, "MEM": cls_mem_minor, "EXC": cls_exc_minor}


def execute_blocks():
    """[(major, minor or None, Block)] in cascade order: major rules first, then minor rules."""
    out_ = []
    for name, fn in SINGLE.items():
        out_.append((MAJ[name], None, fn()))
    for g, fn in COMMON.items():
        b = Block()
        fn(b)
        out_.append((MAJ[g], None, b))
    for g, members in GROUPS.items():
        for i, m in enumerate(members):
            out_.append((MAJ[g], i, MINOR[g](m)))
    return out_
