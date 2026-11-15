"""Builds the FreeRTOS demo into build/rom.bin (code + rodata) and build/ram.bin (initial .data).

The kernel, its ARM_AARCH64_SRE port, heap_4 and FreeRTOS+CLI are unmodified upstream sources.
"""
import pathlib
import subprocess

from sim import machine as M

HERE = pathlib.Path(__file__).parent
OUT = HERE / "build"
CC = "aarch64-linux-gnu-gcc"
PORT = "kernel/portable/ARM_AARCH64_SRE"
SOURCES = ["app/start.S", "app/main.c", "app/board.c", "app/commands.c", "cli/FreeRTOS_CLI.c",
           "kernel/tasks.c", "kernel/queue.c", "kernel/list.c", "kernel/portable/heap_4.c",
           f"{PORT}/port.c", f"{PORT}/portASM.S"]
CFLAGS = ["-Os", "-U_FORTIFY_SOURCE", "-ffreestanding", "-nostdlib", "-fno-builtin",
          "-fno-tree-loop-distribute-patterns", "-mgeneral-regs-only", "-mstrict-align", "-fno-pic", "-fno-pie",
          "-fno-stack-protector", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
          "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections", "-Wall",
          # GUEST selects the port's EL1 variant.
          "-DGUEST",
          "-Iapp", "-Ikernel/include", f"-I{PORT}", "-Icli"]


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, cwd=HERE, **kw)


def build():
    OUT.mkdir(exist_ok=True)
    elf = OUT / "os.elf"
    ld = (HERE / "link.ld.in").read_text()
    for k in ("ROM_BASE", "ROM_SIZE", "RAM_BASE", "RAM_SIZE"):
        ld = ld.replace(k, hex(getattr(M, k)))
    (OUT / "link.ld").write_text(ld)
    run(CC, *CFLAGS, "-T", str(OUT / "link.ld"), "-static", "-no-pie", "-Wl,--build-id=none",
        "-o", str(elf), *SOURCES)
    run("aarch64-linux-gnu-objcopy", "-O", "binary", "-j", ".text", str(elf), str(OUT / "rom.bin"))
    run("aarch64-linux-gnu-objcopy", "-O", "binary", "-j", ".data", str(elf), str(OUT / "ram.bin"))
    (OUT / "os.lst").write_text(run("aarch64-linux-gnu-objdump", "-d", str(elf), capture_output=True,
                                    text=True).stdout)
    (OUT / "os.sym").write_text(run("aarch64-linux-gnu-nm", "-S", "-n", str(elf), capture_output=True,
                                    text=True).stdout)
    rom = (OUT / "rom.bin").read_bytes()
    ram = (OUT / "ram.bin").read_bytes() if (OUT / "ram.bin").exists() else b""
    return rom, ram


if __name__ == "__main__":
    rom, ram = build()
    print(f"rom {len(rom)} bytes, ram image {len(ram)} bytes")
