// Dev-only: cost of one machine iteration's style + layout in Chrome, without the animation machinery.
// Captures real root states while running, freezes animations, then replays the states on the
// root as !important values and times the forced recalc. REMOVE=sel,sel drops subtrees first.
// Usage: [ENGINE=firefox] node tools/itercost.mjs page.html
import * as pw from 'playwright';
import path from 'node:path';

const b = await pw[process.env.ENGINE || 'chromium'].launch();
const p = await b.newPage({ viewport: { width: 1260, height: 720 } });
await p.goto('file://' + path.resolve(process.argv[2]));
await p.waitForTimeout(+(process.env.WAIT || 8000));
const res = await p.evaluate(async ([remove, process_rom_rows]) => {
  const root = document.querySelector('.cpu');
  // The root's latched values are the ones its keyframes (kLS_*, kLR_*) animate.
  const names = [...document.styleSheets[0].cssRules]
    .filter((r) => r instanceof CSSKeyframesRule && /^kL[SR]_/.test(r.name)).map((r) => r.name.slice(1));
  const grab = () => { const cs = getComputedStyle(root); return names.map((n) => [n, cs.getPropertyValue('--' + n)]); };
  const states = [];
  for (let i = 0; i < 12; i++) { states.push(grab()); await new Promise((r) => requestAnimationFrame(r)); }
  document.getAnimations().forEach((a) => a.pause());
  for (const sel of remove) document.querySelectorAll(sel).forEach((e) => e.remove());
  if (process_rom_rows) document.querySelectorAll('.row').forEach((e) => { if (+e.style.getPropertyValue('--j') < 64) (process_rom_rows === 'cells' ? e.querySelectorAll('i') : [e]).forEach((x) => x.remove()); });
  // The rendered subjects: Firefox hides the slots past its own issue width, and styling those
  // on demand would be measured too.
  const probe = [...document.querySelectorAll('.s0')].find((e) => e.getClientRects().length) || root;
  const t = [];
  // Each sample times a batch of 8 iterations, since Firefox's clock only ticks in whole ms.
  for (let k = 0; k < 60; k++) {
    const t0 = performance.now();
    for (let i = 0; i < 8; i++) {
      for (const [n, v] of states[(k * 8 + i) % states.length]) root.style.setProperty('--' + n, v, 'important');
      getComputedStyle(probe).top;
      document.body.offsetTop;
    }
    t.push((performance.now() - t0) / 8);
  }
  t.sort((a, b) => a - b);
  return { n: names.length, median: t[30], p10: t[6], p90: t[54] };
}, [(process.env.REMOVE || '').split(',').filter(Boolean), process.env.NOROM === 'cells' ? 'cells' : process.env.NOROM === '1']);
console.log(`${process.env.ENGINE || 'chromium'} ${path.basename(process.argv[2])} remove=[${process.env.REMOVE || ''}] vars=${res.n} median ${res.median.toFixed(1)} ms (p10 ${res.p10.toFixed(1)}, p90 ${res.p90.toFixed(1)})`);
await b.close();
