"""Differential test: random user-level instructions, one at a time, in Unicorn vs sim/iss.py.

Run: .venv/bin/python -m tests.test_iss_vs_unicorn [iterations-per-class]
"""
import random
import sys

import capstone
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UcError
from unicorn import arm64_const as A

from sim.decode import TABLE, CLASSES, CLS
from sim.iss import Machine, MASK64

MEM = 1 << 20
CODE = 0x1000
DATA = 0x80000

TESTED = ["ADDSUB_IMM", "LOGIC_IMM", "MOVW", "BITFIELD", "EXTR", "ADR", "B", "BCOND", "CB", "TB",
          "LDST_UIMM", "LDST_IMM9", "LDST_REG", "LDR_LIT", "LDSTP", "LOGIC_SREG", "ADDSUB_SREG",
          "ADDSUB_EREG", "ADC", "CCMP", "CSEL", "DP2", "DP1", "DP3", "BR"]
MEMORY_CLASSES = {"LDST_UIMM", "LDST_IMM9", "LDST_REG", "LDSTP", "LDR_LIT"}

cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)


class FlatMachine(Machine):
    """The simulator with a flat 1 MiB memory so random addresses don't need the real map."""

    def __init__(self, mem):
        super().__init__(b"")
        self.flat = mem

    def read_byte(self, addr):
        return self.flat[addr] if addr < MEM else 0

    def store(self, addr, size, value):
        base = addr & ~3
        o = addr & 3
        for i in range(size):
            if o + i < 16 and base + o + i < MEM:
                self.flat[base + o + i] = (value >> (8 * i)) & 0xFF


def keys_for(cls):
    return [k for k in range(2048) if TABLE[k] == CLS[cls]]


def random_value(rng):
    return rng.choice([0, 1, MASK64, 1 << 63, (1 << 31), 0xFFFFFFFF, rng.getrandbits(64),
                       rng.getrandbits(32), rng.getrandbits(8), rng.getrandbits(64), rng.getrandbits(16)])


def make_insn(rng, cls):
    while True:
        insn = (rng.choice(keys_for(cls)) << 21) | rng.getrandbits(21)
        if cls == "BR" and (((insn >> 21) & 15) == 0b0100 or ((insn >> 5) & 31) == 31):
            continue  # ERET is tested via the OS, not here
        if cls == "DP2" and ((insn >> 10) & 63) >= 16:
            continue  # CRC32 is not implemented (it raises an undefined-instruction exception)
        if cls == "B":
            insn = (insn & ~0x3FFFFFF) | (rng.randrange(-0x300, 0x300) & 0x3FFFFFF)
        if cls in ("BCOND", "CB"):
            insn = (insn & ~(0x7FFFF << 5)) | ((rng.randrange(-0x300, 0x300) & 0x7FFFF) << 5)
        if cls == "TB":
            insn = (insn & ~(0x3FFF << 5)) | ((rng.randrange(-0x300, 0x300) & 0x3FFF) << 5)
        if cls in MEMORY_CLASSES:
            if (insn >> 5) & 31 == 31:
                insn &= ~(31 << 5)   # keep SP out of address generation
        dis = list(cs.disasm(insn.to_bytes(4, "little"), CODE))
        if not dis:
            continue
        return insn, f"{dis[0].mnemonic} {dis[0].op_str}"


def run_one(rng, cls):
    insn, text = make_insn(rng, cls)
    regs = [random_value(rng) for _ in range(31)]
    nzcv = rng.getrandbits(4)
    sp = DATA + rng.randrange(0, 0x1000, 16)
    if cls in MEMORY_CLASSES:
        rn = (insn >> 5) & 31
        # Pair accesses must stay inside the 16-byte window, so keep their base aligned.
        regs[rn] = DATA + rng.randrange(0, 0x400, 8 if cls == "LDSTP" else 1)
        if cls == "LDST_REG":
            rm = (insn >> 16) & 31
            if rm != rn and rm != 31:
                regs[rm] = rng.randrange(0, 0x40)
        writeback = (cls == "LDST_IMM9" and ((insn >> 10) & 1)) or (cls == "LDSTP" and ((insn >> 23) & 3) in (1, 3))
        if writeback and rn in (insn & 31, (insn >> 10) & 31 if cls == "LDSTP" else -1):
            return None  # constrained unpredictable
        if cls == "LDSTP" and ((insn >> 22) & 1):
            # Unicorn and real cores disagree on Rt == Rt2 loads; skip those.
            if (insn & 31) == ((insn >> 10) & 31):
                return None
    if cls == "BR":
        regs[(insn >> 5) & 31] = CODE + rng.randrange(0, 0x800, 4)
    mem = bytearray(rng.getrandbits(8) for _ in range(MEM)) if False else bytearray(MEM)
    for a in range(DATA - 0x4000, DATA + 0x8000):
        mem[a] = rng.getrandbits(8)
    for a in range(0, 0x2000):
        mem[a] = rng.getrandbits(8)
    mem[CODE:CODE + 4] = insn.to_bytes(4, "little")

    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0, MEM)
    uc.mem_write(0, bytes(mem))
    for n in range(29):
        uc.reg_write(getattr(A, f"UC_ARM64_REG_X{n}"), regs[n])
    uc.reg_write(A.UC_ARM64_REG_X29, regs[29])
    uc.reg_write(A.UC_ARM64_REG_X30, regs[30])
    uc.reg_write(A.UC_ARM64_REG_SP, sp)
    uc.reg_write(A.UC_ARM64_REG_NZCV, nzcv << 28)
    uc.reg_write(A.UC_ARM64_REG_PC, CODE)
    try:
        uc.emu_start(CODE, CODE + 0x100000, count=1)
    except UcError:
        return None  # faults (e.g. undefined encodings Unicorn rejects) are not compared

    m = FlatMachine(bytearray(mem))
    m.x = list(regs)
    m.sp = [sp, sp]
    m.el, m.spsel = 0, 0
    m.nzcv = nzcv
    m.pc = CODE
    m.step()
    while m.phase:
        m.step()

    diffs = []
    for n in range(31):
        u = uc.reg_read(getattr(A, f"UC_ARM64_REG_X{n}"))
        if u != m.x[n]:
            diffs.append(f"x{n}: uc={u:#x} iss={m.x[n]:#x}")
    if uc.reg_read(A.UC_ARM64_REG_SP) != m.sp[0]:
        diffs.append(f"sp: uc={uc.reg_read(A.UC_ARM64_REG_SP):#x} iss={m.sp[0]:#x}")
    if (uc.reg_read(A.UC_ARM64_REG_NZCV) >> 28) != m.nzcv:
        diffs.append(f"nzcv: uc={uc.reg_read(A.UC_ARM64_REG_NZCV) >> 28:04b} iss={m.nzcv:04b}")
    if uc.reg_read(A.UC_ARM64_REG_PC) != m.pc:
        diffs.append(f"pc: uc={uc.reg_read(A.UC_ARM64_REG_PC):#x} iss={m.pc:#x}")
    if cls in MEMORY_CLASSES:
        um = uc.mem_read(DATA - 0x4000, 0xC000)
        if bytes(um) != bytes(m.flat[DATA - 0x4000:DATA + 0x8000]):
            diffs.append("memory differs")
    return insn, text, diffs


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rng = random.Random(1234)
    failures = 0
    for cls in TESTED:
        ran = bad = 0
        examples = []
        for _ in range(n):
            r = run_one(rng, cls)
            if r is None:
                continue
            ran += 1
            if r[2]:
                bad += 1
                if len(examples) < 3:
                    examples.append(f"    {r[0]:08x} {r[1]}: " + "; ".join(r[2]))
        failures += bad
        print(f"{cls:12s} ran {ran:4d} bad {bad}")
        for e in examples:
            print(e)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
