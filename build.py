"""Builds the FreeRTOS demo and writes index.html, the CSS machine running it.

Run: .venv/bin/python build.py
"""
import re

from cssgen.page import build_page
from freertos.build import HERE, OUT, build


def main():
    rom, ram = build()
    config = (HERE / "app/FreeRTOSConfig.h").read_text()
    tick = int(re.search(r"#define TICK_STEPS\s+(\d+)", config).group(1))
    page = build_page(rom, ram, (OUT / "os.lst").read_text(), (OUT / "os.sym").read_text(), tick)
    with open("index.html", "w") as f:
        f.write(page)
    print(f"index.html: {len(page) / 1e6:.2f} MB, rom {len(rom)} bytes, ram image {len(ram)} bytes")


if __name__ == "__main__":
    main()
