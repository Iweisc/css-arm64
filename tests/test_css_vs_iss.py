"""Differential test: the generated CSS logic (run by cssgen.evaluate) against sim/iss.py.

Each case runs one random instruction from random machine state, including system state,
timer interrupts and exceptions, on the real memory map.
Run: .venv/bin/python -m tests.test_css_vs_iss [iterations-per-class] [class ...]
"""
import random
import sys

from cssgen import cpu, evaluate
from cssgen.evaluate import CssMachine
from sim import machine as M
from sim.decode import CLASSES, CLS, TABLE
from sim.iss import Machine, MASK64

CACHED_CELLS = (M.ROM_BASE // 4, (M.RAM_BASE + M.RAM_SIZE) // 4)
MEMORY_CLASSES = {"LDST_UIMM", "LDST_IMM9", "LDST_REG", "LDSTP", "LDR_LIT"}


def keys_for(cls):
    return [k for k in range(2048) if TABLE[k] == CLS[cls]]


def random_value(rng):
    return rng.choice([0, 1, MASK64, 1 << 63, 1 << 31, 0xFFFFFFFF, rng.getrandbits(64), rng.getrandbits(32),
                       rng.getrandbits(8), rng.getrandbits(64), rng.getrandbits(16)])


def make_case(rng, cls):
    insn = (rng.choice(keys_for(cls)) << 21) | rng.getrandbits(21)
    if cls == "SYS" and rng.random() < 0.5:
        # Aim at a register the machine implements: random keys rarely hit one.
        key = rng.choice(list(M.SYSREGS))
        insn = (insn & ~(0xFFFF << 5)) | (1 << 20) | (key << 5)
    pc = rng.randrange(0x800, 0x3000, 4)
    regs = [random_value(rng) for _ in range(31)]
    sp = [M.RAM_BASE + rng.randrange(0x100, 0xF00, 16) for _ in range(2)]
    if cls == "LDR_LIT" and rng.random() < 0.5:
        # Land the literal in ROM: it is read through the ROM data table, unlike RAM.
        off = rng.randrange(-0x200, 0x200)
        insn = (insn & ~(0x7FFFF << 5)) | ((off & 0x7FFFF) << 5)
    if cls in MEMORY_CLASSES:
        rn = (insn >> 5) & 31
        # Mostly RAM, sometimes ROM (read through the ROM data table).
        region = M.ROM_BASE + 0x400 if rng.random() < 0.25 else M.RAM_BASE
        base = region + rng.randrange(0x100, 0xE00, 8 if cls == "LDSTP" else 1)
        # Cache lines near the base, so some accesses hit and stores merge into them.
        near = lambda: (base >> 2) + rng.randrange(-4, 5)
        lines = [rng.choice([None, near(), near(), rng.randrange(CACHED_CELLS[0], CACHED_CELLS[1] - 3)])
                 for _ in range(M.DCACHE_LINES)]
        lines = [s if s is not None and CACHED_CELLS[0] <= s <= CACHED_CELLS[1] - 4 else None for s in lines]
        if rn == 31:
            sp = [base, base]
        else:
            regs[rn] = base
        if cls == "LDST_REG":
            rm = (insn >> 16) & 31
            if rm not in (rn, 31):
                regs[rm] = rng.randrange(0, 0x40)
    else:
        lines = [None] * M.DCACHE_LINES
    # Some RAM lines hold stores RAM hasn't had yet: RAM under them is stale.
    ram_cells = range(M.RAM_BASE // 4, CACHED_CELLS[1] - 3)
    dirty = [s in ram_cells and rng.random() < 0.5 for s in lines]
    stale = {s + i: (rng.getrandbits(16), rng.getrandbits(16)) for s, d in zip(lines, dirty) if d for i in range(4)}
    if cls == "BR" and (insn >> 5) & 31 < 31:
        regs[(insn >> 5) & 31] = rng.randrange(0x800, 0x3000, 4)
    st = dict(
        regs=regs, sp=sp, pc=pc, nzcv=rng.getrandbits(4), el=rng.choice([0, 1]), spsel=rng.getrandbits(1),
        daif=rng.getrandbits(4), elr=rng.randrange(0x800, 0x3000, 4), spsr=rng.getrandbits(32) & 0xF00003C5,
        esr=rng.getrandbits(32), vbar=rng.choice([0, 0x800, 0x1000]),
        tval=rng.choice([0, 1, 5, 0xFFFFFFFF, rng.getrandbits(32)]), tctl=rng.getrandbits(2),
        cnt=rng.getrandbits(32), kbd=rng.choice([0, 65]), kq=[66 + i for i in range(rng.choice([0, 1, 2, M.KEY_FIFO - 2, M.KEY_FIFO - 1]))],
        pmr=rng.choice([0, M.TIMER_PRIO, M.PMR_MASK, 0x90]), gact=rng.getrandbits(1),
        lines=lines, dirty=dirty, stale=stale, dlr=rng.randrange(M.DCACHE_LINES),
    )
    rom = bytearray(rng.getrandbits(8) for _ in range(M.ROM_SIZE))
    rom[pc:pc + 4] = insn.to_bytes(4, "little")
    ram = bytes(rng.getrandbits(8) for _ in range(M.RAM_SIZE))
    return insn, st, bytes(rom), ram


def setup_iss(st, rom, ram):
    m = Machine(rom, ram)
    m.x = list(st["regs"])
    m.sp = list(st["sp"])
    for k in ("pc", "nzcv", "el", "spsel", "daif", "elr", "spsr", "esr", "vbar", "tval", "tctl", "cnt", "pmr",
              "gact"):
        setattr(m, k, st[k])
    m.dcache = list(st["lines"])
    m.dcache_next = st["dlr"]
    m.key = st["kbd"]
    m.kq = list(st["kq"]) if st["kbd"] else []
    return m


def setup_css(st, rom, ram):
    c = CssMachine(rom, ram)
    S = c.S

    def put(name, v, n=4):
        for k in range(n):
            S[f"{name}_{k}" if n == 4 else f"{name}{k}"] = (v >> (16 * k)) & 0xFFFF

    for i, v in enumerate(st["regs"]):
        put(f"x{i}", v)
    put("sp0", st["sp"][0])
    put("sp1", st["sp"][1])
    for k in ("pc", "elr", "spsr", "esr", "vbar", "tval", "cnt"):
        put(k, st[k], 2)
    for k in ("nzcv", "el", "spsel", "daif", "tctl", "kbd", "pmr", "gact"):
        S[k] = st[k]
    q = (st["kq"] if st["kbd"] else []) + [0] * (2 * len(cpu.KQ))    # the FIFO is only non-empty behind a key
    for j, n in enumerate(cpu.KQ):
        S[n] = q[2 * j] + 128 * q[2 * j + 1]
    S["boot"] = 0
    for L, start in enumerate(st["lines"]):
        if start is not None:
            S[f"dl{L}t"] = start + 1
            S[f"dl{L}d"] = int(st["dirty"][L])
            for i in range(4):
                S[f"dl{L}w{i}0"], S[f"dl{L}w{i}1"] = c.cells[start + i]
    for cell, v in st["stale"].items():
        c.cells[cell] = v
    S["dlr"] = st["dlr"]
    return c


def compare(m, c):
    d = []
    for i in range(31):
        if m.x[i] != c.x(i):
            d.append(f"x{i}: iss={m.x[i]:#x} css={c.x(i):#x}")
    for i, nm in enumerate(("sp0", "sp1")):
        if m.sp[i] != c.limbs(nm):
            d.append(f"{nm}: iss={m.sp[i]:#x} css={c.limbs(nm):#x}")
    for k in ("pc", "elr", "spsr", "esr", "vbar", "tval", "cnt"):
        if getattr(m, k) != c.pair(k):
            d.append(f"{k}: iss={getattr(m, k):#x} css={c.pair(k):#x}")
    for k in ("nzcv", "el", "spsel", "daif", "tctl", "phase", "pmr", "gact"):
        # The CSS's load waits in phase 1 and completes in phase 2, both the ISS's phase 1.
        css = min(c.S[k], 1) if k == "phase" else c.S[k]
        if getattr(m, k) != css:
            d.append(f"{k}: iss={getattr(m, k)} css={css}")
    if m.key != c.S["kbd"]:
        d.append(f"kbd: iss={m.key} css={c.S['kbd']}")
    if m.kq != c.key_queue():
        d.append(f"kq: iss={m.kq} css={c.key_queue()}")
    # The CPU's view of memory: RAM with the dirty cache lines over it.
    seen = c.ram_bytes()
    if bytes(m.ram) != seen:
        diff = [i for i in range(M.RAM_SIZE) if m.ram[i] != seen[i]]
        d.append(f"ram differs at {[hex(M.RAM_BASE + i) for i in diff[:6]]}")
    tags = [c.S[f"dl{L}t"] - 1 if c.S[f"dl{L}t"] else None for L in range(M.DCACHE_LINES)]
    if tags != m.dcache or c.S["dlr"] != m.dcache_next or c.S["dlh"] != m.dcache_hit:
        d.append(f"cache: iss={m.dcache} next {m.dcache_next} hit {m.dcache_hit} "
                 f"css={tags} next {c.S['dlr']} hit {c.S['dlh']}")
    cells = c.visible()
    for L, start in enumerate(tags):
        words = [(c.S[f"dl{L}w{i}0"], c.S[f"dl{L}w{i}1"]) for i in range(4)]
        if start is not None and start + 4 <= CACHED_CELLS[1] and words != cells[start:start + 4]:
            d.append(f"cache line {L} at {start * 4:#x} doesn't match memory: {words} vs {cells[start:start + 4]}")
    if m.output != c.output:
        d.append(f"tx: iss={m.output} css={c.output}")
    return d


def run_one(rng, cls):
    insn, st, rom, ram = make_case(rng, cls)
    m = setup_iss(st, rom, ram)
    c = setup_css(st, rom, ram)
    c.output = []
    for _ in range(4):
        m.step()
        E = c.step()
        if E["O_tx"] >= 0:
            c.output.append(E["O_tx"])
        if m.phase == 0:
            break
    return insn, compare(m, c)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    only = sys.argv[2:]
    rng = random.Random(99)
    failures = 0
    for cls in CLASSES:
        if only and cls not in only:
            continue
        bad, examples = 0, []
        for _ in range(n):
            insn, d = run_one(rng, cls)
            if d:
                bad += 1
                if len(examples) < 3:
                    examples.append(f"    {insn:08x}: " + "; ".join(d[:4]))
        failures += bad
        print(f"{cls:12s} ran {n:4d} bad {bad}", flush=True)
        for e in examples:
            print(e)
    if evaluate.warnings:
        print("f32 precision warnings:", sorted(evaluate.warnings))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
