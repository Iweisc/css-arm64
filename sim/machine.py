"""Machine constants shared by the reference simulator, the CSS generator and the OS build."""

ROM_BASE = 0x0000
ROM_SIZE = 0x4000          # 16 KiB, read/execute only
RAM_BASE = 0x4000
RAM_SIZE = 0x1800          # 6 KiB: every RAM word is a latch the browser keeps alive each frame
DCACHE_LINES = 6           # data cache: each line is the 4-word window a missed load read
MMIO_TX = 0x9000           # write: send one character to the terminal
MMIO_RX = 0x9004           # read: the oldest key pressed and not yet read, 0 if none
KEY_FIFO = 15              # keys the keyboard holds until they are read; more are dropped

TERM_COLS = 48
TERM_ROWS = 20

ESR_SVC = 0x56000000       # EC=0x15 (SVC from AArch64), IL=1, ISS=imm16
ESR_UNDEF = 0x02000000     # EC=0x00, IL=1

# A GICv3 CPU interface with two interrupts at one fixed priority: the physical timer (PPI 30)
# and the keyboard (SPI 33, asserted while a key is latched). PMR implements 5 priority bits.
# An interrupt is signalled while its priority is below PMR and no interrupt is active
# (acknowledged through IAR1 and not yet ended through EOIR1). The timer wins a tie.
TIMER_INTID = 30
KBD_INTID = 33
TIMER_PRIO = 0xF0
PMR_MASK = 0xF8
SPURIOUS_INTID = 1023
IDLE_PRIO = 0xFF


def _sysreg(op0, op1, crn, crm, op2):
    # MRS/MSR carry o0 (op0 - 2), op1, CRn, CRm, op2 in instruction bits [19:5].
    return ((op0 - 2) << 14) | (op1 << 11) | (crn << 7) | (crm << 3) | op2


SYSREGS = {
    _sysreg(3, 3, 4, 2, 0): "NZCV",
    _sysreg(3, 3, 4, 2, 1): "DAIF",
    _sysreg(3, 0, 4, 2, 2): "CurrentEL",
    _sysreg(3, 0, 4, 2, 0): "SPSel",
    _sysreg(3, 0, 4, 0, 1): "ELR_EL1",
    _sysreg(3, 0, 4, 0, 0): "SPSR_EL1",
    _sysreg(3, 0, 4, 1, 0): "SP_EL0",
    _sysreg(3, 0, 5, 2, 0): "ESR_EL1",
    _sysreg(3, 0, 12, 0, 0): "VBAR_EL1",
    _sysreg(3, 3, 14, 2, 0): "CNTP_TVAL_EL0",
    _sysreg(3, 3, 14, 2, 1): "CNTP_CTL_EL0",
    _sysreg(3, 3, 14, 0, 1): "CNTPCT_EL0",
    _sysreg(3, 0, 4, 6, 0): "ICC_PMR_EL1",
    _sysreg(3, 0, 12, 12, 0): "ICC_IAR1_EL1",
    _sysreg(3, 0, 12, 12, 1): "ICC_EOIR1_EL1",
    _sysreg(3, 0, 12, 11, 3): "ICC_RPR_EL1",
}
