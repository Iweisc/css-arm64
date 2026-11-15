"""Reference simulator for the CSS machine.

One call to step() is one CSS frame. It matches the CSS exactly, including the parts that
differ from a real core: loads take two frames, the timer ticks once per frame, memory
accesses go through a 16-byte window, and ELR/VBAR/PC are 32 bits wide.
"""
from . import machine as M
from .decode import TABLE, CLASSES

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def ror(v, r, width):
    r %= width
    v &= (1 << width) - 1
    return ((v >> r) | (v << (width - r))) & ((1 << width) - 1)


def decode_bit_masks(n, imms, immr, width):
    """Returns (wmask, tmask) as in the ARM pseudocode, for logical immediates and bitfields."""
    combined = (n << 6) | (~imms & 0x3F)
    length = combined.bit_length() - 1
    if length < 1:
        return None
    esize = 1 << length
    levels = esize - 1
    s, r = imms & levels, immr & levels
    d = (s - r) & levels
    welem = (1 << (s + 1)) - 1
    telem = (1 << (d + 1)) - 1
    wmask = 0
    tmask = 0
    welem = ror(welem, r, esize)
    for i in range(0, width, esize):
        wmask |= welem << i
        tmask |= telem << i
    return wmask & ((1 << width) - 1), tmask & ((1 << width) - 1)


COND_NAMES = ["EQ", "NE", "CS", "CC", "MI", "PL", "VS", "VC", "HI", "LS", "GE", "LT", "GT", "LE", "AL", "NV"]


class Machine:
    def __init__(self, rom: bytes, ram_init: bytes = b""):
        assert len(rom) <= M.ROM_SIZE
        self.rom = bytearray(rom) + bytearray(M.ROM_SIZE - len(rom))
        self.ram = bytearray(M.RAM_SIZE)
        self.ram[: len(ram_init)] = ram_init
        self.x = [0] * 31
        self.sp = [0, 0]           # SP_EL0, SP_EL1
        self.pc = 0
        self.nzcv = 0
        self.el = 1
        self.spsel = 1
        self.daif = 0xF
        self.elr = 0
        self.spsr = 0
        self.esr = 0
        self.vbar = 0
        self.tval = 0
        self.tctl = 0
        self.pmr = 0
        self.gact = 0              # the timer interrupt is active in the GIC
        self.cnt = 0
        self.phase = 0
        # Data cache tags: the first word (addr >> 2) of each line, or None. The cache is
        # write-through, so a line's data is always RAM's: the simulator only needs the tags.
        self.dcache = [None] * M.DCACHE_LINES
        self.dcache_next = 0
        self.dcache_hit = 0        # whether the last RAM load hit, for the page's light
        self.key = 0
        self.kq = []        # keys waiting behind key, oldest first (KEY_FIFO - 1 at most)
        self.term_col = 0
        self.term_row = 0
        self.term_top = 0
        self.screen = [[32] * M.TERM_COLS for _ in range(M.TERM_ROWS)]
        self.output = []           # every character sent to TX, for tests
        self.steps = 0
        self.retired = 0

    # --- memory -----------------------------------------------------------------------------
    def read_byte(self, addr):
        if M.ROM_BASE <= addr < M.ROM_BASE + M.ROM_SIZE:
            return self.rom[addr - M.ROM_BASE]
        if M.RAM_BASE <= addr < M.RAM_BASE + M.RAM_SIZE:
            return self.ram[addr - M.RAM_BASE]
        return 0

    def window(self, addr):
        """The 16 bytes the CSS data port sees: four 32-bit cells starting at addr & ~3."""
        base = addr & ~3
        return [self.read_byte((base + i) & MASK32) for i in range(16)]

    def load(self, addr, size, extra=0):
        """Reads `size` bytes at byte `extra` past addr, from the window that starts at addr."""
        addr &= MASK64
        if addr == M.MMIO_RX:
            # Reading the keyboard takes the key and moves the FIFO up.
            if extra:
                return 0
            v = self.key
            self.key = self.kq.pop(0) if self.kq else 0
            return v
        w = self.window(addr)
        o = (addr & 3) + extra
        v = 0
        for i in range(size):
            j = o + i
            v |= (w[j] if j < 16 else 0) << (8 * i)
        return v

    @staticmethod
    def cacheable(addr):
        """ROM and RAM, which sit next to each other from address 0."""
        return addr & MASK64 < M.RAM_BASE + M.RAM_SIZE

    def cached(self, addr, nbytes):
        """Whether a load of nbytes at addr finds all its words in one cache line. A hit
        completes in one step instead of two."""
        addr &= MASK64
        if not self.cacheable(addr):
            return False
        first, last = addr >> 2, (addr + nbytes - 1) >> 2
        return any(s is not None and s <= first and last <= s + 3 for s in self.dcache)

    def cache_fill(self, addr):
        """A missed load brings in the 4-word window it read, replacing lines in turn."""
        if self.cacheable(addr):
            self.dcache[self.dcache_next] = (addr & MASK64) >> 2
            self.dcache_next = (self.dcache_next + 1) % M.DCACHE_LINES

    def load_phase(self, addr, nbytes):
        """Loads take two steps unless the cache has them. Returns True when the load should
        wait (phase 0 of a miss); otherwise records the hit or fills a line on the second step."""
        if self.phase == 0:
            hit = self.cached(addr, nbytes)
            if self.cacheable(addr):
                self.dcache_hit = int(hit)
            if not hit:
                self.phase = 1
                return True
            return False
        self.cache_fill(addr)
        return False

    def store(self, addr, size, value):
        addr &= MASK64
        if addr == M.MMIO_TX:
            self.term_put(value & 0xFF)
            return
        base = addr & ~3
        o = addr & 3
        for i in range(size):
            j = o + i
            if j >= 16:
                break
            a = base + j
            if M.RAM_BASE <= a < M.RAM_BASE + M.RAM_SIZE:
                self.ram[a - M.RAM_BASE] = (value >> (8 * i)) & 0xFF

    def fetch(self, addr):
        """Instructions come from ROM only (the CSS fetches through a ROM lookup table); anywhere
        else reads as 0, which is undefined and raises an exception."""
        if addr & 3 or not M.ROM_BASE <= addr < M.ROM_BASE + M.ROM_SIZE:
            return 0
        return sum(self.read_byte(addr + i) << (8 * i) for i in range(4))

    # --- terminal ---------------------------------------------------------------------------
    def term_put(self, c):
        self.output.append(c)
        R, C = M.TERM_ROWS, M.TERM_COLS
        if c == 10:
            self.term_col = 0
            self.term_newline()
        elif c == 13:
            self.term_col = 0
        elif c == 8:
            self.term_col = max(self.term_col - 1, 0)
        elif c == 12:
            self.screen = [[32] * C for _ in range(R)]
            self.term_col = self.term_row = self.term_top = 0
        elif 32 <= c < 127:
            self.screen[(self.term_top + self.term_row) % R][self.term_col] = c
            self.term_col += 1
            if self.term_col == C:
                self.term_col = 0
                self.term_newline()

    def term_newline(self):
        R = M.TERM_ROWS
        if self.term_row < R - 1:
            self.term_row += 1
        else:
            self.screen[self.term_top] = [32] * M.TERM_COLS
            self.term_top = (self.term_top + 1) % R

    def screen_text(self):
        R = M.TERM_ROWS
        rows = [self.screen[(self.term_top + v) % R] for v in range(R)]
        return "\n".join("".join(chr(c) for c in row).rstrip() for row in rows)

    # --- registers --------------------------------------------------------------------------
    def sp_index(self):
        return 1 if (self.el == 1 and self.spsel == 1) else 0

    def rx(self, n, sp=False):
        if n == 31:
            return self.sp[self.sp_index()] if sp else 0
        return self.x[n]

    def wx(self, n, v, sp=False, width=64):
        v &= MASK64 if width == 64 else MASK32
        if n == 31:
            if sp:
                self.sp[self.sp_index()] = v
            return
        self.x[n] = v

    def cond(self, c):
        n, z, cf, v = (self.nzcv >> 3) & 1, (self.nzcv >> 2) & 1, (self.nzcv >> 1) & 1, self.nzcv & 1
        base = [z, cf, n, v, cf and not z, n == v, (n == v) and not z, 1][c >> 1]
        r = bool(base)
        if (c & 1) and c != 15:
            r = not r
        return r

    def pstate_bits(self):
        m = 0 if self.el == 0 else (0b0101 if self.spsel else 0b0100)
        return (self.nzcv << 28) | (self.daif << 6) | m

    # --- arithmetic helpers -----------------------------------------------------------------
    @staticmethod
    def add_with_carry(a, b, cin, width):
        mask = (1 << width) - 1
        a &= mask
        b &= mask
        u = a + b + cin
        r = u & mask
        n = r >> (width - 1)
        z = int(r == 0)
        c = u >> width
        sa, sb, sr = a >> (width - 1), b >> (width - 1), n
        v = int(sa == sb and sr != sa)
        return r, (n << 3) | (z << 2) | (c << 1) | v

    @staticmethod
    def shift(v, typ, amt, width):
        mask = (1 << width) - 1
        v &= mask
        amt %= width
        if typ == 0:
            return (v << amt) & mask
        if typ == 1:
            return v >> amt
        if typ == 2:
            return (sext(v, width) >> amt) & mask
        return ror(v, amt, width)

    @staticmethod
    def extend(v, option, width):
        size = 8 << (option & 3)
        v &= (1 << size) - 1 if size < 64 else MASK64
        if option & 4:
            v = sext(v, size) & MASK64
        return v & ((1 << width) - 1)

    # --- exceptions -------------------------------------------------------------------------
    def take_exception(self, kind, return_addr, esr=None):
        if self.el == 0:
            base = 0x400
        elif self.spsel:
            base = 0x200
        else:
            base = 0x000
        off = base + (0x80 if kind == "irq" else 0)
        self.spsr = self.pstate_bits()
        self.elr = return_addr & MASK32
        if esr is not None:
            self.esr = esr
        self.el, self.spsel, self.daif = 1, 1, 0xF
        self.pc = (self.vbar + off) & MASK32

    def press(self, k):
        """A key press: queued behind any unread keys, dropped when KEY_FIFO are waiting."""
        if not self.key:
            self.key = k
        elif len(self.kq) < M.KEY_FIFO - 1:
            self.kq.append(k)

    def timer_asserted(self):
        enable, imask = self.tctl & 1, (self.tctl >> 1) & 1
        return bool(enable and not imask and sext(self.tval, 32) <= 0)

    def gic_pending(self):
        asserted = self.timer_asserted() or self.key != 0
        return asserted and M.TIMER_PRIO < self.pmr and not self.gact

    def irq_pending(self):
        return self.gic_pending() and not (self.daif & 0b0010)

    # --- system registers -------------------------------------------------------------------
    def mrs(self, key):
        name = M.SYSREGS.get(key)
        if name == "ICC_IAR1_EL1":
            if not self.gic_pending():
                return M.SPURIOUS_INTID
            self.gact = 1
            return M.TIMER_INTID if self.timer_asserted() else M.KBD_INTID
        return {
            "NZCV": self.nzcv << 28,
            "DAIF": self.daif << 6,
            "CurrentEL": self.el << 2,
            "SPSel": self.spsel,
            "ELR_EL1": self.elr,
            "SPSR_EL1": self.spsr,
            "SP_EL0": self.sp[0],
            "ESR_EL1": self.esr,
            "VBAR_EL1": self.vbar,
            "CNTP_TVAL_EL0": self.tval,
            "CNTP_CTL_EL0": (self.tctl & 3) | (4 if sext(self.tval, 32) <= 0 else 0),
            "CNTPCT_EL0": self.cnt,
            "ICC_PMR_EL1": self.pmr,
            "ICC_RPR_EL1": M.TIMER_PRIO if self.gact else M.IDLE_PRIO,
        }.get(name, 0)

    def msr(self, key, v):
        name = M.SYSREGS.get(key)
        if name == "NZCV":
            self.nzcv = (v >> 28) & 0xF
        elif name == "DAIF":
            self.daif = (v >> 6) & 0xF
        elif name == "SPSel":
            self.spsel = v & 1
        elif name == "ELR_EL1":
            self.elr = v & MASK32
        elif name == "SPSR_EL1":
            self.spsr = v & MASK32
        elif name == "SP_EL0":
            self.sp[0] = v & MASK64
        elif name == "ESR_EL1":
            self.esr = v & MASK32
        elif name == "VBAR_EL1":
            self.vbar = v & MASK32
        elif name == "CNTP_TVAL_EL0":
            return ("tval", v & MASK32)
        elif name == "CNTP_CTL_EL0":
            self.tctl = v & 3
        elif name == "ICC_PMR_EL1":
            self.pmr = v & M.PMR_MASK
        elif name == "ICC_EOIR1_EL1":
            self.gact = 0
        return None

    # --- main step --------------------------------------------------------------------------
    def step(self):
        tval_write = None
        if self.phase == 0 and self.irq_pending():
            self.take_exception("irq", self.pc)
        else:
            insn = self.fetch(self.pc)
            tval_write = self.execute(insn)
        self.steps += 1
        self.cnt = (self.cnt + 1) & MASK32
        self.tval = tval_write if tval_write is not None else (self.tval - 1) & MASK32

    def execute(self, insn):
        cls = CLASSES[TABLE[insn >> 21]]
        f = getattr(self, "op_" + cls)
        r = f(insn)
        if self.phase == 0:
            self.retired += 1
        return r

    def next(self):
        self.pc = (self.pc + 4) & MASK32

    # Classes ----------------------------------------------------------------------------------
    def op_UNDEF(self, i):
        self.take_exception("sync", self.pc, M.ESR_UNDEF)

    def op_SVC(self, i):
        self.take_exception("sync", self.pc + 4, M.ESR_SVC | ((i >> 5) & 0xFFFF))

    def op_ADDSUB_IMM(self, i):
        sf, op, s, sh = i >> 31, (i >> 30) & 1, (i >> 29) & 1, (i >> 22) & 1
        imm = ((i >> 10) & 0xFFF) << (12 * sh)
        rn, rd = (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        a = self.rx(rn, sp=True)
        b = (~imm if op else imm) & ((1 << w) - 1)
        r, flags = self.add_with_carry(a, b, op, w)
        if s:
            self.nzcv = flags
        self.wx(rd, r, sp=not s, width=w)
        self.next()

    def op_ADDSUB_SREG(self, i):
        sf, op, s = i >> 31, (i >> 30) & 1, (i >> 29) & 1
        typ, rm, amt = (i >> 22) & 3, (i >> 16) & 31, (i >> 10) & 63
        rn, rd = (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        b = self.shift(self.rx(rm), typ, amt, w)
        b = (~b if op else b) & ((1 << w) - 1)
        r, flags = self.add_with_carry(self.rx(rn), b, op, w)
        if s:
            self.nzcv = flags
        self.wx(rd, r, width=w)
        self.next()

    def op_ADDSUB_EREG(self, i):
        sf, op, s = i >> 31, (i >> 30) & 1, (i >> 29) & 1
        rm, option, imm3 = (i >> 16) & 31, (i >> 13) & 7, (i >> 10) & 7
        rn, rd = (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        b = (self.extend(self.rx(rm), option, w) << imm3) & ((1 << w) - 1)
        b = (~b if op else b) & ((1 << w) - 1)
        r, flags = self.add_with_carry(self.rx(rn, sp=True), b, op, w)
        if s:
            self.nzcv = flags
        self.wx(rd, r, sp=not s, width=w)
        self.next()

    def op_ADC(self, i):
        sf, op, s = i >> 31, (i >> 30) & 1, (i >> 29) & 1
        rm, rn, rd = (i >> 16) & 31, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        b = self.rx(rm) & ((1 << w) - 1)
        b = (~b if op else b) & ((1 << w) - 1)
        r, flags = self.add_with_carry(self.rx(rn), b, (self.nzcv >> 1) & 1, w)
        if s:
            self.nzcv = flags
        self.wx(rd, r, width=w)
        self.next()

    def logic(self, opc, a, b, w):
        r = [a & b, a | b, a ^ b, a & b][opc] & ((1 << w) - 1)
        return r

    def logic_flags(self, r, w):
        return ((r >> (w - 1)) << 3) | (int(r == 0) << 2)

    def op_LOGIC_IMM(self, i):
        sf, opc, n = i >> 31, (i >> 29) & 3, (i >> 22) & 1
        immr, imms, rn, rd = (i >> 16) & 63, (i >> 10) & 63, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        masks = decode_bit_masks(n, imms, immr, w)
        imm = masks[0] if masks else 0
        r = self.logic(opc, self.rx(rn), imm, w)
        if opc == 3:
            self.nzcv = self.logic_flags(r, w)
        self.wx(rd, r, sp=opc != 3, width=w)
        self.next()

    def op_LOGIC_SREG(self, i):
        sf, opc, typ, n = i >> 31, (i >> 29) & 3, (i >> 22) & 3, (i >> 21) & 1
        rm, amt, rn, rd = (i >> 16) & 31, (i >> 10) & 63, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        b = self.shift(self.rx(rm), typ, amt, w)
        if n:
            b = ~b & ((1 << w) - 1)
        r = self.logic(opc, self.rx(rn), b, w)
        if opc == 3:
            self.nzcv = self.logic_flags(r, w)
        self.wx(rd, r, width=w)
        self.next()

    def op_MOVW(self, i):
        sf, opc, hw = i >> 31, (i >> 29) & 3, (i >> 21) & 3
        imm, rd = (i >> 5) & 0xFFFF, i & 31
        w = 64 if sf else 32
        sh = 16 * hw
        if opc == 0:
            r = ~(imm << sh)
        elif opc == 2:
            r = imm << sh
        else:
            r = (self.rx(rd) & ~(0xFFFF << sh)) | (imm << sh)
        self.wx(rd, r, width=w)
        self.next()

    def op_BITFIELD(self, i):
        sf, opc, n = i >> 31, (i >> 29) & 3, (i >> 22) & 1
        immr, imms, rn, rd = (i >> 16) & 63, (i >> 10) & 63, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        mask = (1 << w) - 1
        masks = decode_bit_masks(n, imms, immr, w)
        wmask, tmask = masks if masks else (0, 0)
        src = self.rx(rn) & mask
        dst = self.rx(rd) & mask if opc == 1 else 0
        bot = (dst & ~wmask) | (ror(src, immr, w) & wmask)
        if opc == 0:
            top = mask if (src >> imms) & 1 else 0
        else:
            top = dst
        if opc == 2:
            r = bot & tmask
        else:
            r = (top & ~tmask) | (bot & tmask)
        self.wx(rd, r & mask, width=w)
        self.next()

    def op_EXTR(self, i):
        sf, rm, lsb, rn, rd = i >> 31, (i >> 16) & 31, (i >> 10) & 63, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        mask = (1 << w) - 1
        cat = ((self.rx(rn) & mask) << w) | (self.rx(rm) & mask)
        self.wx(rd, (cat >> lsb) & mask, width=w)
        self.next()

    def op_ADR(self, i):
        op, immlo, immhi, rd = i >> 31, (i >> 29) & 3, (i >> 5) & 0x7FFFF, i & 31
        imm = sext((immhi << 2) | immlo, 21)
        if op:
            r = (self.pc & ~0xFFF) + (imm << 12)
        else:
            r = self.pc + imm
        self.wx(rd, r)
        self.next()

    def op_B(self, i):
        if i >> 31:
            self.x[30] = (self.pc + 4) & MASK64
        self.pc = (self.pc + (sext(i & 0x3FFFFFF, 26) << 2)) & MASK32

    def op_BCOND(self, i):
        if self.cond(i & 15):
            self.pc = (self.pc + (sext((i >> 5) & 0x7FFFF, 19) << 2)) & MASK32
        else:
            self.next()

    def op_CB(self, i):
        sf, op, rt = i >> 31, (i >> 24) & 1, i & 31
        v = self.rx(rt) & (MASK64 if sf else MASK32)
        if (v == 0) != bool(op):
            self.pc = (self.pc + (sext((i >> 5) & 0x7FFFF, 19) << 2)) & MASK32
        else:
            self.next()

    def op_TB(self, i):
        b = ((i >> 31) << 5) | ((i >> 19) & 31)
        op, rt = (i >> 24) & 1, i & 31
        bit = (self.rx(rt) >> b) & 1
        if bit == op:
            self.pc = (self.pc + (sext((i >> 5) & 0x3FFF, 14) << 2)) & MASK32
        else:
            self.next()

    def op_BR(self, i):
        opc, rn = (i >> 21) & 15, (i >> 5) & 31
        if opc == 0b0100:
            spsr = self.spsr
            self.pc = self.elr & MASK32
            self.nzcv = (spsr >> 28) & 0xF
            self.daif = (spsr >> 6) & 0xF
            self.el = (spsr >> 2) & 1
            self.spsel = spsr & 1
            return
        target = self.rx(rn) & MASK32
        if opc == 0b0001:
            self.x[30] = (self.pc + 4) & MASK64
        self.pc = target

    def op_SYS(self, i):
        load, op0 = (i >> 21) & 1, (i >> 19) & 3
        rt = i & 31
        if op0 >= 2:
            key = (i >> 5) & 0x7FFF
            if load:
                self.wx(rt, self.mrs(key))
            else:
                r = self.msr(key, self.rx(rt))
                if r:
                    self.next()
                    return r[1]
        elif op0 == 0 and ((i >> 12) & 15) == 4:
            op1, op2, crm = (i >> 16) & 7, (i >> 5) & 7, (i >> 8) & 15
            if op1 == 0 and op2 == 5:
                self.spsel = crm & 1
            elif op1 == 3 and op2 == 6:
                self.daif |= crm
            elif op1 == 3 and op2 == 7:
                self.daif &= ~crm & 0xF
        self.next()

    def op_CSEL(self, i):
        sf, op, rm, c = i >> 31, (i >> 30) & 1, (i >> 16) & 31, (i >> 12) & 15
        o2, rn, rd = (i >> 10) & 1, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        if self.cond(c):
            r = self.rx(rn)
        else:
            r = self.rx(rm)
            if op:
                r = ~r
            if o2:
                r = r + 1
        self.wx(rd, r, width=w)
        self.next()

    def op_CCMP(self, i):
        sf, op, rm, c = i >> 31, (i >> 30) & 1, (i >> 16) & 31, (i >> 12) & 15
        imm_form, rn, nzcv = (i >> 11) & 1, (i >> 5) & 31, i & 15
        w = 64 if sf else 32
        if self.cond(c):
            b = rm if imm_form else self.rx(rm)
            b &= (1 << w) - 1
            if op:
                b = ~b & ((1 << w) - 1)
            _, flags = self.add_with_carry(self.rx(rn), b, op, w)
            self.nzcv = flags
        else:
            self.nzcv = nzcv
        self.next()

    def op_DP2(self, i):
        sf, rm, opcode, rn, rd = i >> 31, (i >> 16) & 31, (i >> 10) & 63, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        mask = (1 << w) - 1
        a, b = self.rx(rn) & mask, self.rx(rm) & mask
        if opcode == 2:
            r = a // b if b else 0
        elif opcode == 3:
            sa, sb = sext(a, w), sext(b, w)
            if sb == 0:
                r = 0
            else:
                q = abs(sa) // abs(sb)
                r = q if (sa < 0) == (sb < 0) else -q
        elif 8 <= opcode <= 11:
            r = self.shift(a, opcode - 8, b % w, w)
        else:
            return self.op_UNDEF(i)
        self.wx(rd, r & mask, width=w)
        self.next()

    def op_DP1(self, i):
        sf, opcode, rn, rd = i >> 31, (i >> 10) & 63, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        a = self.rx(rn) & ((1 << w) - 1)
        if opcode == 0:
            r = int(format(a, f"0{w}b")[::-1], 2)
        elif opcode in (1, 2, 3):
            # REV16 / REV32 / REV: reverse bytes within containers of 16, 32, 64 bits.
            csize = {1: 16, 2: 32, 3: 64}[opcode] if sf else {1: 16, 2: 32, 3: 32}[opcode]
            if not sf and opcode == 3:
                return self.op_UNDEF(i)
            r = 0
            for base in range(0, w, csize):
                c = (a >> base) & ((1 << csize) - 1)
                rc = int.from_bytes(c.to_bytes(csize // 8, "little"), "big")
                r |= rc << base
        elif opcode == 4:
            r = w - a.bit_length()
        elif opcode == 5:
            s = sext(a, w)
            r = (w - (s if s >= 0 else ~s).bit_length()) - 1
        else:
            return self.op_UNDEF(i)
        self.wx(rd, r, width=w)
        self.next()

    def op_DP3(self, i):
        sf, op31, rm, o0 = i >> 31, (i >> 21) & 7, (i >> 16) & 31, (i >> 15) & 1
        ra, rn, rd = (i >> 10) & 31, (i >> 5) & 31, i & 31
        w = 64 if sf else 32
        mask = (1 << w) - 1
        a, b, c = self.rx(rn), self.rx(rm), self.rx(ra)
        if op31 == 0:
            p = (a & mask) * (b & mask)
            r = c - p if o0 else c + p
        elif op31 in (1, 5) and sf:
            if op31 == 1:
                p = sext(a, 32) * sext(b, 32)
            else:
                p = (a & MASK32) * (b & MASK32)
            r = c - p if o0 else c + p
        elif op31 == 2 and sf and not o0:
            r = (sext(a, 64) * sext(b, 64)) >> 64
        elif op31 == 6 and sf and not o0:
            r = (a * b) >> 64
        else:
            return self.op_UNDEF(i)
        self.wx(rd, r & mask, width=w)
        self.next()

    # Loads and stores -----------------------------------------------------------------------
    def ldst_common(self, i, size, opc, addr, wb):
        """size: log2 bytes; opc: 0 store, 1 load, 2 load signed to 64, 3 load signed to 32.
        Loads take two frames. wb is (reg, value) for base writeback or None."""
        rt = i & 31
        nbytes = 1 << size
        if opc == 2 and size == 3:
            # PRFM: no-op
            self.next()
            return
        if opc == 0:
            self.store(addr, nbytes, self.rx(rt))
            if wb:
                self.wx(wb[0], wb[1], sp=True)
            self.next()
            return
        if self.load_phase(addr, nbytes):
            return
        v = self.load(addr, nbytes)
        if opc == 2:
            v = sext(v, 8 * nbytes) & MASK64
        elif opc == 3:
            v = sext(v, 8 * nbytes) & MASK32
        if wb:
            self.wx(wb[0], wb[1], sp=True)
        self.wx(rt, v)
        self.phase = 0
        self.next()

    def op_LDST_UIMM(self, i):
        size, opc = i >> 30, (i >> 22) & 3
        rn = (i >> 5) & 31
        addr = (self.rx(rn, sp=True) + (((i >> 10) & 0xFFF) << size)) & MASK64
        self.ldst_common(i, size, opc, addr, None)

    def op_LDST_IMM9(self, i):
        size, opc, mode = i >> 30, (i >> 22) & 3, (i >> 10) & 3
        rn = (i >> 5) & 31
        base = self.rx(rn, sp=True)
        imm = sext((i >> 12) & 0x1FF, 9)
        moved = (base + imm) & MASK64
        if mode == 1:
            self.ldst_common(i, size, opc, base, (rn, moved))
        elif mode == 3:
            self.ldst_common(i, size, opc, moved, (rn, moved))
        else:
            self.ldst_common(i, size, opc, moved, None)

    def op_LDST_REG(self, i):
        size, opc = i >> 30, (i >> 22) & 3
        rm, option, s, rn = (i >> 16) & 31, (i >> 13) & 7, (i >> 12) & 1, (i >> 5) & 31
        off = (self.extend(self.rx(rm), option, 64) << (size if s else 0)) & MASK64
        addr = (self.rx(rn, sp=True) + off) & MASK64
        self.ldst_common(i, size, opc, addr, None)

    def op_LDR_LIT(self, i):
        opc = i >> 30
        addr = (self.pc + (sext((i >> 5) & 0x7FFFF, 19) << 2)) & MASK64
        if opc == 3:
            self.next()
            return
        size, kind = [(2, 1), (3, 1), (2, 2)][opc]
        self.ldst_common(i, size, kind, addr, None)

    def op_LDSTP(self, i):
        opc, mode, load = i >> 30, (i >> 23) & 3, (i >> 22) & 1
        imm7, rt2, rn, rt = sext((i >> 15) & 0x7F, 7), (i >> 10) & 31, (i >> 5) & 31, i & 31
        size = 3 if opc == 2 else 2
        nb = 1 << size
        base = self.rx(rn, sp=True)
        moved = (base + (imm7 << size)) & MASK64
        addr = base if mode == 1 else moved
        wb = (rn, moved) if mode in (1, 3) else None
        if not load:
            both = (self.rx(rt) & ((1 << (8 * nb)) - 1)) | ((self.rx(rt2) & ((1 << (8 * nb)) - 1)) << (8 * nb))
            self.store(addr, 2 * nb, both)
            if wb:
                self.wx(wb[0], wb[1], sp=True)
            self.next()
            return
        if self.load_phase(addr, 2 * nb):
            return
        v1, v2 = self.load(addr, nb), self.load(addr, nb, extra=nb)
        if opc == 1:
            v1, v2 = sext(v1, 32) & MASK64, sext(v2, 32) & MASK64
        if wb:
            self.wx(wb[0], wb[1], sp=True)
        self.wx(rt, v1)
        self.wx(rt2, v2)
        self.phase = 0
        self.next()
