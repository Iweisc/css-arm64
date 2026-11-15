"""Dev-only: checks tools/reach.mjs output lines against the simulator: the pc the page showed at
a step count must be the simulator's pc after that many steps (or the step before, since the page
is read between frames). Usage: node tools/reach.mjs ... | .venv/bin/python -m tools.reachcheck
"""
import re
import sys

from freertos.build import build
from sim.iss import Machine

rom, ram = build()
lines = sys.stdin.read().splitlines()
want = {int(m.group(1)): None for line in lines if (m := re.search(r"at step (\d+) pc=", line))}
m, pcs = Machine(rom, ram), {}
for n in range(max(want, default=0) + 1):
    pcs[n] = m.pc
    m.step()
for line in lines:
    m_ = re.search(r"at step (\d+) pc=([0-9a-f]+)", line)
    ok = m_ and int(m_.group(2), 16) in (pcs[int(m_.group(1))], pcs.get(int(m_.group(1)) - 1))
    print(line, "OK" if ok else "WRONG (the page didn't do the work)")
