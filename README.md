<img src="assets/icon.svg" width="64" height="64" alt="">

# css-arm64

An AArch64 (ARMv8-A) CPU written in HTML and CSS, with no JavaScript, running [FreeRTOS](https://www.freertos.org). Open [index.html](index.html) in Chrome or Firefox and the machine boots on its own. Type at the FreeRTOS+CLI shell with the on-screen keyboard. Beside the terminal you can watch the CPU work: every register, a live disassembly around pc, maps of ROM and RAM, the data cache's lines, and which issue slots ran each frame.

## The machine

- **CPU:** A64 integer instruction set at EL0 and EL1. It covers arithmetic, logic, shifts, bitfields, multiply and divide, loads and stores of every size including pairs and pre/post-index, branches, conditional select and compare, `svc`, `eret`, and the system registers below. Floating point and SIMD instructions raise an undefined-instruction exception.
- **Exceptions:** `VBAR_EL1`, `ELR_EL1`, `SPSR_EL1`, `ESR_EL1`, `SP_EL0`/`SP_EL1`, `SPSel` and `DAIF`. There are synchronous and IRQ vectors for EL1t, EL1h and lower-EL entry.
- **Interrupts:** a GICv3 CPU interface (`ICC_PMR_EL1`, `ICC_IAR1_EL1`, `ICC_EOIR1_EL1`, `ICC_RPR_EL1`) with two interrupts at priority `0xF0`: the timer (PPI 30) and the keyboard (SPI 33).
- **Timer:** `CNTP_TVAL_EL0` counts down one per instruction step and asserts PPI 30 at zero when `CNTP_CTL_EL0` enables it. `CNTPCT_EL0` counts steps.
- **Memory:**
  - 16 KB ROM at `0x0000`, holding the OS image. Instructions are fetched from ROM only.
  - 6 KB RAM at `0x4000`.
  - A terminal at `0x9000` (write a byte to print it).
  - A keyboard at `0x9004`. It queues up to 15 key presses. A read takes the oldest, and the interrupt stays asserted while any are waiting.
- **Terminal:** 48×20 characters, with newline, carriage return, backspace, form feed (clear) and scrolling.

## How it works

Everything is in [index.html](index.html). The generator in [cssgen/](cssgen) writes it.

- **State.** Each 16-bit value of machine state is the position of a 1px element inside a hidden scroller. A `view-timeline` on that element drives an animation on the root whose value is the position. Each frame the CPU reads where the elements were placed last frame and places them at the next state. Registers, flags, pc, system registers, timer, GIC and terminal cursor all work this way.
- **Decode.** A size container is `insn[31:21]` pixels big, and container queries map it to an instruction class. A second container queries that class and applies one rule per class. All other logic is `calc()` arithmetic on 16-bit limbs with `mod()`, `round()`, `pow()`, `clamp()` and `abs()`.
- **Fetch.** ROM never changes, so it is a lookup table in the stylesheet. A container is as wide as pc's 64-word ROM row, and one container query per row hands that row down. A second container is as tall as pc's column, and one query per column picks the word. Fetch happens in the same pass, so branches cost nothing extra.
- **Registers.** The register file is indexed, not muxed. The read ports' register numbers size two containers, and one container query per register hands that register's limbs to the port. Write-back works the same way: the write ports' targets size containers, and one rule per register takes the written value. Only the rules for the registers in use apply.
- **Several instructions per frame.** The decode and execute levels are nested 8 deep in Chrome and 4 in Firefox (chosen with `@supports`), and each issue slot starts from the one before's next state. A slot runs unless an earlier one is waiting for a load, or it needs the memory bus after an earlier slot used it. The bus takes one access a frame and memory acts on it the next frame, so it works as a one-entry store buffer: a store doesn't stop the slots after it.
- **Data cache.** Six 16-byte lines hold windows of ROM and RAM that loads missed. A load the cache holds completes in its slot without the bus. The cache is write-back: a store that one line holds only updates the cache and marks the line dirty, and a dirty line is written back over the bus when a miss replaces it. A miss reads memory with any newer words from the cache patched in.
- **Memory.**
  - **ROM data.** Loads from ROM read a second table, keyed by the data address in 16-word rows. Each row also carries the next 3 words, so a 16-byte window is always in one row.
  - **RAM.** Each RAM row latches its 64 cells' values through their view timelines. Rows are grouped into banks, and a bank only passes the bus on when the access falls inside it, so idle rows aren't restyled.
  - **Reads.** The address bus sizes the row container that holds it, and container queries give the selected cells `anchor-name`s. Readers anchored to them are the timeline subjects the CPU reads, a 16-byte window.
  - **Timing.** Most instructions take one slot of a frame. A load that misses the cache takes three frames: it puts its address on the bus, waits a frame while memory places its readers, then reads them. Stall frames don't count as steps.
- **I/O.** The terminal is one self-latching cell per character, drawn with a 128-glyph `@counter-style`. Keys are `:active` buttons matched with `:has()`.
- **Disassembly.** ROM never changes, so the build turns the OS's objdump listing into two `@counter-style`s indexed by word, one for the enclosing function and one for the instruction. Counters set from pc pick the lines to show.

Some engine behaviour shaped the design:

- **Restyling.** Both browsers restyle a whole subtree when an ancestor's inherited custom properties change. So the CPU's values inherit only inside the CPU's own tree, where everything changes every frame anyway, and the root's latched copies (`--LS_*`) don't inherit: memory and the panels pass on only what they read. Firefox also restyles everything under a size container that changes size, so memory and the terminal sit outside the decode containers and read the bus one frame later.
- **Lookups in Firefox.** Firefox evaluates the CPU's lookups as container style queries on the index (`style(--qromw: 5)`) rather than size queries. They need no layout per level, which makes each issue slot much cheaper there. Firefox doesn't re-evaluate style queries when an animated value changes, so the first slot is also a size container whose width changes every frame, and everything under it is matched again. Chrome is faster with size queries.
- **Precision.** Firefox evaluates `calc()` in single precision, so no intermediate value exceeds 2^24. Chrome passes a registered value through `var()` as text with 6 significant digits, so no value stored in a property reaches a million. Larger offsets stay inside one `calc()`.
- **Latch timing.** Both browsers update view timelines once per rendered frame and re-run style and layout once more if they changed, so the machine gets at most two iterations per frame: 120 a second on a 60 Hz display.

## FreeRTOS

[freertos/](freertos) holds unmodified upstream sources: the FreeRTOS kernel (V11.1.0+, commit in [freertos/kernel/VERSION](freertos/kernel/VERSION)), its `ARM_AARCH64_SRE` port in EL1 mode, `heap_4`, and FreeRTOS+CLI. They are built with `aarch64-linux-gnu-gcc`. [freertos/app/](freertos/app) is the board support and demo:

- the tick interrupt every 2,000 steps from the timer
- a keyboard interrupt handler that takes every waiting key, echoes and edits the line, and wakes the shell with a task notification on Enter
- a FreeRTOS+CLI shell with `help ps uptime free echo primes count kill clear`
- `primes` and `count` start background tasks, and `kill` deletes them

## Building and testing

You need Python 3, `aarch64-linux-gnu-gcc` and binutils. The tests also need `unicorn` and `capstone` in `.venv`.

```sh
python build.py                          # builds FreeRTOS and writes index.html
python -m sim.run 20000 'ps\n'           # boots FreeRTOS in the reference simulator
.venv/bin/python -m tests.test_iss_vs_unicorn 300   # simulator vs Unicorn, per instruction class
.venv/bin/python -m tests.test_css_vs_iss 300       # the generated CSS logic vs the simulator
.venv/bin/python -m tests.test_css_boot 16000 'primes 30\nps\n'  # CSS logic vs simulator, full boot, step by step
.venv/bin/python -m tests.test_css_keys            # fast typing through the CSS logic's own key handling
```

[sim/iss.py](sim/iss.py) is the reference model the CSS is held to. `cssgen.evaluate` runs the stylesheet's own expressions in Python with the same memory and terminal behaviour. Both are checked against each other every step through a full FreeRTOS boot. [tools/](tools) holds the Playwright scripts used to run the page and measure it in real browsers.

## Speed

Measured headless on a 60 Hz display, FreeRTOS gets 2,788 steps into its boot in 42 seconds in Chrome and 39 in Firefox. Before the issue slots, the data cache and the store buffer, the same point took 99 and 59 seconds, so Chrome runs 2.4 times faster and Firefox 1.5 times. A step is an instruction or one half of a load that misses the cache, and the shell prompt appears after 2,389 steps. At the idle prompt the machine runs about 150 steps a second, and busy tasks run 35 to 60. [tools/reach.mjs](tools/reach.mjs) times a run to a step count, and [tools/reachcheck.py](tools/reachcheck.py) checks that the page's pc there matches the simulator's.

The clock sets the ceiling: view timelines update once per rendered frame, so the machine gets at most 120 iterations a second at 60 Hz. Each issue slot costs Chrome about a millisecond of style per iteration and Firefox more, which is why they run 8 and 4. In Chrome nearly half of each iteration goes to keeping RAM's 1,536 cells and 3,072 view timelines alive, which is why RAM is 6 KB: the stacks and heap are sized from measured use, and three background tasks still fit. Loads that miss the cache take three frames, and 86% of them are followed straight away by an instruction that needs the loaded value or the bus, so letting later instructions run past a miss would gain little. [tools/itercost.mjs](tools/itercost.mjs) and [tools/stylecost.mjs](tools/stylecost.mjs) measure the parts of a frame, and [tools/statecheck.py](tools/statecheck.py) checks the page against `cssgen.evaluate` frame by frame.
