"""Dev-only: checks the page's CPU against the CSS evaluator, frame by frame, from real boot states.

Runs the evaluator from boot, keeps every frame's starting state, readers and expected next state,
then has tools/statecheck.mjs put each starting state on the page's root (animations paused) and
read what the page computes. Prints the first frames whose next state differs.
Usage: .venv/bin/python -m tools.statecheck [first frame] [frames] [engine]
"""
import json
import os
import subprocess
import sys

from cssgen import cpu
from cssgen.evaluate import CssMachine
from freertos.build import build

first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
count = int(sys.argv[2]) if len(sys.argv) > 2 else 200
engine = sys.argv[3] if len(sys.argv) > 3 else "chromium"

if engine == "firefox":
    cpu.ISSUE = cpu.ISSUE_FIREFOX
rom, ram = build()
c = CssMachine(rom, ram)
cases = []
while c.frames < first + count:
    # The page's readers hold what memory showed at the start of the frame: the model's R
    # before the frame runs.
    start, reads, n = dict(c.S), dict(c.R), c.frames
    c.frame()
    if n >= first:
        cases.append({"frame": n, "S": start, "R": reads, "N": dict(c.S)})
out = subprocess.run(["node", "tools/statecheck.mjs", engine], input=json.dumps(cases), capture_output=True, text=True)
if out.returncode:
    sys.exit(out.stderr)
if "DUMP" in os.environ:
    sys.exit(out.stdout)
bad = 0
for case, got in zip(cases, json.loads(out.stdout)):
    diff = {k: (got[k], v) for k, v in case["N"].items() if got.get(k) != v}
    if diff:
        bad += 1
        if bad <= 5:
            print(f"frame {case['frame']} pc={case['S']['pc0']:#x} phase={case['S']['phase']}: page vs model {diff}")
print(f"{len(cases)} frames checked, {bad} differ")
