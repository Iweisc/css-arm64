"""Boots FreeRTOS in the CSS logic (cssgen.evaluate) and the ISS in lockstep, comparing every step.

Run: .venv/bin/python -m tests.test_css_boot [steps] [typed text]
"""
import sys

from freertos.build import build
from cssgen.evaluate import CssMachine
from sim.iss import Machine
from sim.run import Typist
from tests.test_css_vs_iss import compare


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    typist = Typist(sys.argv[2] if len(sys.argv) > 2 else "")
    rom, ram = build()
    m, c = Machine(rom, ram), CssMachine(rom, ram)
    c.output = []
    for n in range(steps):
        k = typist.next_key(m)
        if k:
            m.press(k)
            c.press(k)
        m.step()
        E = c.step()
        if E["O_tx"] >= 0:
            c.output.append(E["O_tx"])
        d = compare(m, c)
        if c.screen_text() != m.screen_text():
            d.append("screen differs")
        if d:
            print(f"step {n} pc={m.pc:#x}: " + "; ".join(d[:6]))
            sys.exit(1)
    print(c.screen_text())
    print(f"--- {steps} steps match, {m.retired} instructions, {c.frames} frames")


if __name__ == "__main__":
    main()
