"""Dev-only: finds the first step where a browser recording (tools/record.mjs) disagrees with the ISS."""
import json, sys
from freertos.build import build
from sim.iss import Machine

rec = json.load(open(sys.argv[1]))
rom, ram = build()
m = Machine(rom, ram)
limbs = lambda v: [(v >> (16 * k)) & 0xFFFF for k in range(4)]
states = {0: None}
by_step = {}
for r in rec:
    by_step.setdefault(r[0], r)
last = max(by_step)
hist = []
for n in range(last + 1):
    hist.append((m.pc, limbs(m.x[0]), limbs(m.x[1]), limbs(m.x[30]), limbs(m.sp[1]), m.nzcv, m.el, m.phase))
    m.step()
bad = 0
for n in sorted(by_step):
    r = by_step[n]
    exp = hist[n]
    # CSS phases 1 and 2 are both the ISS's phase 1 (a load waiting for memory).
    got = (r[1], r[2], r[3], r[4], r[5], r[6], r[7], min(r[8], 1))
    if tuple(got) != tuple(exp):
        print("first mismatch at step", n, "\n got", got, "\n iss", exp)
        print("previous recorded steps:", [s for s in sorted(by_step) if s < n][-5:])
        bad = 1
        break
print("checked", len(by_step), "distinct steps up to", last, "ok" if not bad else "")
