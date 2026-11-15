"""Types at the shell through the CSS logic's own key handling, as the page sees a held on-screen
key (cssgen.evaluate's key), faster than the OS reads them. test_css_boot puts keys straight in
the FIFO, so this is the test of key edges and queueing.

Run: .venv/bin/python -m tests.test_css_keys
"""
import sys

from freertos.build import build
from cssgen.evaluate import CssMachine

TEXT = "primes 30\n"
HELD, GAP = 4, 20      # frames a key is down, then up: a quick typist at the page's frame rate


def main():
    c = CssMachine(*build())
    while not c.screen_text().rstrip().endswith("$"):
        c.frame()
    for ch in TEXT:
        c.key = ord(ch)
        for _ in range(HELD):
            c.frame()
        c.key = 0
        for _ in range(GAP):
            c.frame()
    for _ in range(300):
        c.frame()
    typed = next(line for line in c.screen_text().split("\n") if line.startswith("$ "))
    if typed != "$ " + TEXT.strip():
        print(f"typed {TEXT!r}, the shell shows {typed!r}")
        sys.exit(1)
    print(f"--- the shell shows {typed!r}")


if __name__ == "__main__":
    main()
