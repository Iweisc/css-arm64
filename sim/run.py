"""Boots the OS in the reference simulator and prints the terminal.

Usage: python -m sim.run [steps] [typed text]
Typed text uses `\\n` for Enter. Each line is typed once the shell shows its prompt.
"""
import sys

from freertos.build import build
from . import machine as M
from .iss import Machine


class Typist:
    """Types like a person: a line starts only after the "$ " prompt, and keys come GAP steps
    apart whether or not the OS has read the last one yet (the keyboard queues M.KEY_FIFO)."""

    GAP = 50

    def __init__(self, text):
        self.keys = list(text.replace("\\n", "\n"))
        self.line_start = True
        self.idle = 0

    def next_key(self, m):
        if not self.keys or len(m.kq) == M.KEY_FIFO - 1:
            return None
        self.idle += 1
        if self.idle <= self.GAP or (self.line_start and bytes(m.output[-2:]) != b"$ "):
            return None
        self.idle = 0
        k = self.keys.pop(0)
        self.line_start = k == "\n"
        return ord(k)


def boot(steps, typed=""):
    rom, ram = build()
    m = Machine(rom, ram)
    typist = Typist(typed)
    for _ in range(steps):
        k = typist.next_key(m)
        if k:
            m.press(k)
        m.step()
    return m


if __name__ == "__main__":
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    m = boot(steps, sys.argv[2] if len(sys.argv) > 2 else "")
    print(m.screen_text())
    print(f"--- {m.steps} steps, {m.retired} instructions, pc={m.pc:#x}")
