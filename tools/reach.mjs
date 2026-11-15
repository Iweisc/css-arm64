// Dev-only: seconds from page load until the CPU's step counter (--S_cnt0/1 on .st) reaches a target.
// Usage: ENGINES=chromium,firefox node tools/reach.mjs page.html steps
import { chromium, firefox } from 'playwright';
import path from 'node:path';
const engines = { chromium, firefox };
const [file, target] = [process.argv[2], +process.argv[3]];
for (const name of (process.env.ENGINES || 'chromium').split(',')) {
  const b = await engines[name].launch();
  const p = await b.newPage();
  const t0 = Date.now();
  await p.goto('file://' + path.resolve(file));
  // Step count and pc together, so the time can be checked against the simulator (tools/reachcheck.py):
  // a page that counts steps without doing the work would look fast.
  const read = () => p.evaluate(() => { const s = getComputedStyle(document.querySelector('.st')); const g = (n) => +s.getPropertyValue('--S_' + n); return [g('cnt0') + 65536 * g('cnt1'), g('pc0') + 65536 * g('pc1')]; });
  let r;
  while ((r = await read())[0] < target) await p.waitForTimeout(250);
  console.log(name.padEnd(9), path.basename(file).padEnd(26), target, 'steps in', ((Date.now() - t0) / 1000).toFixed(1), 's', `(at step ${r[0]} pc=${r[1].toString(16)})`);
  await b.close();
}
