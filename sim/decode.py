"""Instruction classes. The CSS decoder uses the same table, keyed by instruction bits [31:21]."""

CLASSES = [
    "UNDEF", "ADDSUB_IMM", "LOGIC_IMM", "MOVW", "BITFIELD", "EXTR", "ADR", "B", "BCOND", "CB", "TB",
    "SVC", "SYS", "BR", "LDST_UIMM", "LDST_IMM9", "LDST_REG", "LDR_LIT", "LDSTP", "LOGIC_SREG",
    "ADDSUB_SREG", "ADDSUB_EREG", "ADC", "CCMP", "CSEL", "DP2", "DP1", "DP3",
]
CLS = {name: i for i, name in enumerate(CLASSES)}
# Not reachable from the table: injected when an interrupt is taken.
CLS["IRQ"] = len(CLASSES)


def classify(key):
    """key = instruction bits [31:21]."""
    insn = key << 21

    def bits(hi, lo):
        return (insn >> lo) & ((1 << (hi - lo + 1)) - 1)

    if bits(28, 23) == 0b100010:
        return "ADDSUB_IMM"
    if bits(28, 23) == 0b100100:
        return "LOGIC_IMM"
    if bits(28, 23) == 0b100101:
        return "UNDEF" if bits(30, 29) == 0b01 else "MOVW"
    if bits(28, 23) == 0b100110:
        return "UNDEF" if bits(30, 29) == 0b11 else "BITFIELD"
    if bits(28, 23) == 0b100111:
        return "EXTR" if bits(30, 29) == 0 and bits(21, 21) == 0 else "UNDEF"
    if bits(28, 24) == 0b10000:
        return "ADR"
    if bits(30, 26) == 0b00101:
        return "B"
    if bits(31, 24) == 0b01010100:
        return "BCOND"
    if bits(30, 25) == 0b011010:
        return "CB"
    if bits(30, 25) == 0b011011:
        return "TB"
    if bits(31, 21) == 0b11010100000:
        return "SVC"
    if bits(31, 22) == 0b1101010100:
        return "SYS"
    if bits(31, 25) == 0b1101011:
        return "BR" if bits(24, 21) in (0b0000, 0b0001, 0b0010, 0b0100) else "UNDEF"
    if bits(27, 27) == 1 and bits(25, 25) == 0:
        # Loads and stores (general-purpose registers only; bit 26 set means SIMD/FP).
        if bits(26, 26):
            return "UNDEF"
        if bits(29, 28) == 0b11:
            if bits(31, 30) >= 2 and bits(23, 22) == 0b11:
                return "UNDEF"
            if bits(24, 24):
                return "LDST_UIMM"
            return "LDST_REG" if bits(21, 21) else "LDST_IMM9"
        if bits(29, 28) == 0b01 and bits(24, 24) == 0:
            return "LDR_LIT"
        if bits(29, 28) == 0b10:
            opc, load = bits(31, 30), bits(22, 22)
            if opc == 0b11 or (opc == 0b01 and not load):
                return "UNDEF"
            return "LDSTP"
        return "UNDEF"
    if bits(28, 24) == 0b01010:
        return "LOGIC_SREG"
    if bits(28, 24) == 0b01011:
        return "ADDSUB_EREG" if bits(21, 21) else "ADDSUB_SREG"
    if bits(28, 21) == 0b11010000:
        return "ADC"
    if bits(28, 21) == 0b11010010 and bits(29, 29) == 1:
        return "CCMP"
    if bits(28, 21) == 0b11010100 and bits(29, 29) == 0:
        return "CSEL"
    if bits(28, 21) == 0b11010110 and bits(29, 29) == 0:
        return "DP1" if bits(30, 30) else "DP2"
    if bits(28, 24) == 0b11011 and bits(30, 29) == 0:
        return "DP3"
    return "UNDEF"


TABLE = [CLS[classify(k)] for k in range(2048)]
