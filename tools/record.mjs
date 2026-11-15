// Dev-only: records [steps, pc, x0, x1, x30, sp1, nzcv, el, phase, ...extra] on every animation frame.
// EXTRA=a,b,... appends those state values (--S_a ...).
import { chromium, firefox } from 'playwright';
import path from 'node:path';
import fs from 'node:fs';
const engines = { chromium, firefox };
const [name, secs, out] = [process.argv[2], +process.argv[3], process.argv[4]];
const b = await engines[name].launch();
const p = await b.newPage();
await p.addInitScript((extra) => {
  window.__rec = [];
  const tick = () => {
    const st = document.querySelector('.st');
    if (st) {
      const s = getComputedStyle(st); const g = (n) => +s.getPropertyValue('--S_' + n);
      const root = getComputedStyle(document.querySelector('.cpu'));
      const L = (n) => [0, 1, 2, 3].map((k) => g(`${n}_${k}`));
      window.__rec.push([g('cnt0') + 65536 * g('cnt1'), g('pc0') + 65536 * g('pc1'), L('x0'), L('x1'), L('x30'), L('sp1'), g('nzcv'), g('el'), g('phase'), ...extra.map((n) => +root.getPropertyValue('--LS_' + n))]);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}, (process.env.EXTRA || '').split(',').filter(Boolean));
await p.goto('file://' + path.resolve('index.html'));
await p.waitForTimeout(secs * 1000);
fs.writeFileSync(out, JSON.stringify(await p.evaluate(() => window.__rec)));
await b.close();
