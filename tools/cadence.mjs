// Dev-only: rendered frames per second and CPU steps per frame (from --S_cnt0 on .st).
import { chromium, firefox } from 'playwright';
import path from 'node:path';
const engines = { chromium, firefox };
for (const name of (process.env.ENGINES || 'chromium').split(',')) {
  // UNCAP=1 lifts the 60 fps cap so the rate shows the real per-frame cost (measurement only).
  const uncap = process.env.UNCAP === '1';
  const b = await engines[name].launch(uncap ? (name === 'firefox' ? { firefoxUserPrefs: { 'layout.frame_rate': 0 } } : { args: ['--disable-gpu-vsync', '--disable-frame-rate-limit'] }) : {});
  const p = await b.newPage();
  // REMOVE=sel,sel drops subtrees before the machine starts, to see what they cost (the program may misbehave).
  const remove = (process.env.REMOVE || '').split(',').filter(Boolean);
  if (remove.length) await p.addInitScript((sels) => document.addEventListener('DOMContentLoaded', () => sels.forEach((s) => document.querySelectorAll(s).forEach((e) => e.remove()))), remove);
  await p.goto('file://' + path.resolve(process.argv[2]));
  // WAIT=seconds measures later in the boot (default 3).
  await p.waitForTimeout(1000 * +(process.env.WAIT || 3));
  const r = await p.evaluate(() => new Promise((done) => {
    const cnt = () => { const s = getComputedStyle(document.querySelector('.st')); return +s.getPropertyValue('--S_cnt0'); };
    let frames = 0; const c0 = cnt(); const t0 = performance.now();
    const tick = () => { frames++; if (performance.now() - t0 < 5000) requestAnimationFrame(tick); else done([frames, cnt() - c0, performance.now() - t0]); };
    requestAnimationFrame(tick);
  }));
  const [frames, steps, ms] = r;
  console.log(name.padEnd(9), process.env.REMOVE || '', (frames / ms * 1000).toFixed(1), 'frames/s', (steps / frames).toFixed(2), 'steps/frame', (steps / ms * 1000).toFixed(1), 'steps/s');
  await b.close();
}
