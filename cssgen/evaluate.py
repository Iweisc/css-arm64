"""Runs the generated CSS logic in Python, frame by frame, to test it without a browser.

The CPU expressions are the exact strings that go into the stylesheet. The memory, terminal and
latch glue is mirrored here by hand, following what the page's cell rules do.
"""
import math
import re

from sim import machine as M
from sim.decode import CLASSES, TABLE

from . import cpu
from .props import REG

VAR = re.compile(r"var\(--([\w-]+)\)")
F32_LIMIT = 1 << 24
warnings = set()


def _chk(x, what):
    if abs(x) >= F32_LIMIT:
        warnings.add(what)
    return x


def FL(x):
    return math.floor(_chk(x, "floor"))


def RN(x):
    return math.floor(_chk(x, "round") + 0.5)


def MD(a, b):
    _chk(a, "mod")
    return a - b * math.floor(a / b)


def CL(lo, x, hi):
    return max(lo, min(x, hi))


def PW(a, b):
    return a ** b


def translate(expr):
    e = str(expr)
    e = e.replace("round(down,", "FL(").replace("round(", "RN(").replace("mod(", "MD(")
    e = e.replace("clamp(", "CL(").replace("pow(", "PW(").replace("calc(", "(")
    return VAR.sub(lambda m: f'E["{m.group(1)}"]', e)


class Stage:
    """One element's declarations, ordered so each is evaluated after the ones it uses."""

    def __init__(self, decls, visible, level):
        self.items = []
        names = {}
        for prop, value in decls:
            names[prop[2:]] = value          # later declarations win, like the cascade
        deps = {n: set(VAR.findall(str(v))) & names.keys() for n, v in names.items()}
        for n, v in names.items():
            for d in VAR.findall(str(v)):
                if d not in names and d not in visible:
                    raise ValueError(f"{level}: --{n} uses --{d}, which isn't visible here")
        done, order = set(), []

        def visit(n, stack=()):
            if n in done:
                return
            if n in stack:
                raise ValueError(f"cycle at --{n}")
            for d in deps[n]:
                visit(d, stack + (n,))
            done.add(n)
            order.append(n)

        for n in names:
            visit(n)
        for n in order:
            self.items.append((n, compile(translate(names[n]), n, "eval")))
        self.names = set(names)

    def run(self, E):
        g = {"FL": FL, "RN": RN, "MD": MD, "CL": CL, "PW": PW, "E": E}
        for n, code in self.items:
            v = E[n] = RN(eval(code, g))
            # Chrome passes a registered value on through var() as text with 6 significant
            # digits, so a value of a million or more that isn't round reaches its users wrong.
            if abs(v) >= 1_000_000 and float(f"{v:.6g}") != v:
                warnings.add(f"6 digits: --{n}")


class CssMachine:
    def __init__(self, rom, ram_init=b""):
        rom = bytes(rom) + bytes(M.ROM_SIZE - len(rom))
        ram = bytes(ram_init) + bytes(M.RAM_SIZE - len(ram_init))
        mem = rom + ram
        self.cells = [(mem[i] | mem[i + 1] << 8, mem[i + 2] | mem[i + 3] << 8) for i in range(0, len(mem), 4)]
        self.ncells = len(self.cells)
        self.ram_first = M.RAM_BASE // 4
        self.S = dict(cpu.STATE_INIT)
        self.R = {f"R_d{k}{h}": 0 for k in range(4) for h in (0, 1)}
        self.key = 0
        self.screen = [[32] * M.TERM_COLS for _ in range(M.TERM_ROWS)]
        self.frames = 0
        self._frames = None
        self._build()

    _shared = None

    def _build(self):
        # The compiled stages don't depend on the program, so every machine shares them.
        if CssMachine._shared is None:
            CssMachine._shared = self._compile()
        self.__dict__.update(CssMachine._shared)

    def _compile(self):
        self = type("Compiled", (), {})()
        st = cpu.cpu_block()
        c = cpu.c_block()
        self.defaults = cpu.defaults_block()
        self.post = cpu.post_block()
        self.exec_blocks = cpu.execute_blocks()
        base = {f"S_{n}" for n, _ in cpu.STATE} | {f"F{c}{h}" for c in range(cpu.ROM_ROW) for h in "01"} \
            | {f"Q{i}{h}" for i in range(cpu.DROM_SPAN) for h in "01"} | {f"R_d{k}{h}" for k in range(4) for h in (0, 1)} | {"key"}
        base |= {"I0", "I1"} | set(cpu.CARRY) | {f"DQ{k}{h}" for k in range(4) for h in "01"}
        self.st = Stage(st.decls, base, ".st")
        vis = base | self.st.names | {f"{p}{k}" for p, _ in cpu.READ_PORTS for k in range(4)}
        self.dw = Stage(cpu.data_window().decls, vis, ".dk")
        vis |= self.dw.names
        self.c = Stage(c.decls, vis | {"tmaj", "tmin"}, ".c")
        self.vis_e = vis | self.c.names
        self.e_cache = {}
        self.initial = {n: spec[2] for n, spec in REG.props.items()}
        return dict(vars(self))

    def e_stage(self, maj, mn):
        key = (maj, mn)
        if key not in self.e_cache:
            decls = list(self.defaults.decls)
            for bmaj, bmin, blk in self.exec_blocks:
                if bmaj == maj and bmin is None:
                    decls += blk.decls
            for bmaj, bmin, blk in self.exec_blocks:
                if bmaj == maj and bmin == mn:
                    decls += blk.decls
            decls += self.post.decls
            self.e_cache[key] = Stage(decls, self.vis_e, f".e {key}")
        return self.e_cache[key]

    def evaluate(self, carry=None):
        """One issue slot, from state self.S, the readers' values self.R, and the values an
        earlier slot this frame passed on (cpu.CARRY; the first slot's when None)."""
        E = dict(self.initial)
        E.update({f"S_{n}": v for n, v in self.S.items()})
        E.update(carry or {n: v for n, (_, v) in cpu.CARRY.items()})
        E.update(self.R)
        E.update(self.rom_row(self.S["pc0"], self.S["pc1"]))
        E.update(self.rom_data_row(self.S["bd"] - cpu.BUS_BIAS["bd"]))
        E["key"] = self.key
        col = (self.S["pc0"] // 4) % cpu.ROM_ROW
        E["I0"], E["I1"] = E[f"F{col}0"], E[f"F{col}1"]
        off = (self.S["bd"] - cpu.BUS_BIAS["bd"]) % cpu.DROM_ROW
        for k in range(4):
            for h in "01":
                E[f"DQ{k}{h}"] = E[f"Q{off + k}{h}"]
        self.st.run(E)
        self.read_ports(E)
        self.dw.run(E)
        k = E["k1"] * 8 + E["k2"]
        mm = cpu.major_minor(CLASSES[TABLE[k]])
        E["tmaj"], E["tmin"] = mm
        self.c.run(E)
        self.e_stage(E["maj"], E["min"]).run(E)
        self.write_back(E)
        return E

    def read_ports(self, E):
        """What the register file's read rules (cssgen.page.register_file) give .pb and .dk."""
        for port, idx in cpu.READ_PORTS:
            r = E[idx]
            for k in range(4):
                E[f"{port}{k}"] = E[f"S_x{r}_{k}"] if r < 31 else 0

    def write_back(self, E):
        """What the register file's write rules give the subjects: the latched value, unless a
        write port targets the register (later ports in cpu.WRITE_ORDER win)."""
        for r, n in cpu.REGFILE:
            for k in range(4):
                v = E[f"S_{r}_{k}"]
                for w in cpu.WRITE_ORDER:
                    if E[f"{w}t"] == n:
                        v = E[f"O_{w}v{k}"]
                E[f"N_{r}_{k}"] = v

    def rom_row(self, pc0, pc1):
        """What the ROM table (cssgen.page.rom_table) gives .st for pc: the ROM row holding pc as
        --F{col}{half}, or nothing (all 0) when pc isn't a word-aligned ROM address."""
        R = cpu.ROM_ROW
        i = pc0 // 4
        ok = pc1 == 0 and pc0 % 4 == 0 and i < self.ram_first
        row = self.cells[i - i % R:i - i % R + R] if ok else [(0, 0)] * R
        return {f"F{c}{h}": w[k] for c, w in enumerate(row) for k, h in enumerate("01")}

    def press(self, k):
        """A key arriving straight in the keyboard FIFO, as cpu.post_block queues it."""
        q = self.key_queue()
        if not self.S["kbd"]:
            self.S["kbd"] = k
        elif len(q) < M.KEY_FIFO - 1:
            q.append(k)
        q += [0] * (2 * len(cpu.KQ) - len(q))
        for j, n in enumerate(cpu.KQ):
            self.S[n] = q[2 * j] + 128 * q[2 * j + 1]

    def key_queue(self):
        """The keys waiting behind kbd, oldest first (sim.iss.Machine.kq)."""
        q = [self.S[n] >> s & 127 for n in cpu.KQ for s in (0, 7)]
        return q[:q.index(0)] if 0 in q else q

    def rom_data_row(self, cell):
        """What the ROM data table (cssgen.page.rom_data_table) gives .st for the bus cell."""
        R, span = cpu.DROM_ROW, cpu.DROM_SPAN
        out = {f"Q{i}{h}": 0 for i in range(span) for h in "01"}
        if 0 <= cell < self.ram_first:
            first = cell - cell % R
            for i in range(span):
                if first + i < self.ram_first:
                    out[f"Q{i}0"], out[f"Q{i}1"] = self.cells[first + i]
        return out

    def cell(self, i):
        return self.cells[i] if 0 <= i < self.ncells else (0, 0)

    def apply_bus(self, cells, screen, S):
        """What memory and the terminal do with the bus values latched in state S."""
        if S["bst"]:
            d = S["bd"] - cpu.BUS_BIAS["bd"]
            for k in range(4):
                i = d + k
                if self.ram_first <= i < self.ncells:
                    lo, hi = cells[i]
                    m = S["bsm"] >> (4 * k) & 15
                    nlo = S[f"bsv{k}l"] + (1 - (m & 1)) * (lo & 0xFF) + (1 - (m >> 1 & 1)) * (lo & 0xFF00)
                    nhi = S[f"bsv{k}h"] + (1 - (m >> 2 & 1)) * (hi & 0xFF) + (1 - (m >> 3 & 1)) * (hi & 0xFF00)
                    cells[i] = (nlo, nhi)
        wrow, clr = S["bwr"] - cpu.BUS_BIAS["bwr"], S["bcl"] - cpu.BUS_BIAS["bcl"]
        for r in range(M.TERM_ROWS):
            for col in range(M.TERM_COLS):
                if r == wrow and col == S["bwc"]:
                    screen[r][col] = S["bch"]
                elif r == clr or S["bff"]:
                    screen[r][col] = 32

    def slots(self):
        """One frame as the page runs it: memory and the terminal act on the bus values latched
        at the start of the frame, and the issue slots run one after another from that state.
        Yields each slot that did architectural work (not a stall or a hold)."""
        S = self.S
        self.apply_bus(self.cells, self.screen, S)
        # Memory's readers go to the latched bus address this frame, and the next frame sees
        # what they read. They only see RAM; ROM words come from the ROM data table.
        d = S["bd"] - cpu.BUS_BIAS["bd"]
        reads = {}
        for k in range(4):
            ram = self.ram_first <= d + k < self.ncells
            reads[f"R_d{k}0"], reads[f"R_d{k}1"] = self.cell(d + k) if ram else (0, 0)
        carry = None
        for _ in range(cpu.ISSUE):
            E = self.evaluate(carry)
            self.S = {n: E[f"N_{n}"] for n in S}
            carry = {n: E[nn] for n, (nn, _) in cpu.CARRY.items()}
            if not E["stall"] and not E["hold"]:
                yield E
        self.R = reads
        self.frames += 1

    def frame(self):
        return list(self.slots())

    def run_frames(self):
        while True:
            yield from self.slots()

    def step(self):
        """Runs until an issue slot does architectural work; returns its values."""
        if self._frames is None:
            self._frames = self.run_frames()
        return next(self._frames)

    # --- views matching sim.iss.Machine -----------------------------------------------------------
    def limbs(self, name):
        return sum(self.S[f"{name}_{k}"] << (16 * k) for k in range(4))

    def x(self, i):
        return self.limbs(f"x{i}")

    def pair(self, name):
        return self.S[f"{name}0"] | self.S[f"{name}1"] << 16

    def pending(self):
        """Memory and screen with the bus values not yet acted on applied."""
        cells, screen = list(self.cells), [list(r) for r in self.screen]
        self.apply_bus(cells, screen, self.S)
        return cells, screen

    def screen_text(self):
        R = M.TERM_ROWS
        top = self.S["ttop"]
        screen = self.pending()[1]
        rows = [screen[(top + v) % R] for v in range(R)]
        return "\n".join("".join(chr(c) for c in row).rstrip() for row in rows)

    def visible(self):
        """Memory as the CPU sees it: pending() with the dirty cache lines' words over RAM's."""
        cells = self.pending()[0]
        for L in range(M.DCACHE_LINES):
            start = self.S[f"dl{L}t"] - 1
            if start >= 0 and self.S[f"dl{L}d"]:
                for i in range(4):
                    if start + i < self.ncells:
                        cells[start + i] = (self.S[f"dl{L}w{i}0"], self.S[f"dl{L}w{i}1"])
        return cells

    def ram_bytes(self):
        out = bytearray()
        for lo, hi in self.visible()[self.ram_first:]:
            out += bytes([lo & 0xFF, lo >> 8, hi & 0xFF, hi >> 8])
        return bytes(out)
