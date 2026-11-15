// Dev-only: per-frame style/layout cost in Chrome for each page given.
// Usage: [REMOVE=sel,sel] node tools/stylecost.mjs a.html [b.html ...]
// Prints, per page: steps/s, and mean ms per frame of style recalc (by element count), animation and layout.
import { chromium } from 'playwright';
import path from 'node:path';
import fs from 'node:fs';

for (const file of process.argv.slice(2)) {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1260, height: 720 } });
  await p.goto('file://' + path.resolve(file));
  await p.waitForTimeout(+(process.env.WAIT || 4000));
  // REMOVE=sel,sel drops those subtrees first, to see what they cost (the machine may then misbehave).
  if (process.env.REMOVE) await p.evaluate((sels) => sels.forEach((s) => document.querySelectorAll(s).forEach((e) => e.remove())), process.env.REMOVE.split(','));
  const cnt = () => p.evaluate(() => {
    const s = getComputedStyle(document.querySelector('.st'));
    return +s.getPropertyValue('--S_cnt0') + 65536 * +s.getPropertyValue('--S_cnt1') || 0;
  });
  const c0 = await cnt(), t0 = Date.now();
  await p.waitForTimeout(4000);
  const rate = ((await cnt()) - c0) / ((Date.now() - t0) / 1000);
  await b.startTracing(p, { path: '/tmp/stylecost.json', categories: ['devtools.timeline', 'blink', 'cc', 'disabled-by-default-devtools.timeline'] });
  await p.waitForTimeout(3000);
  await b.stopTracing();
  await b.close();
  const ev = JSON.parse(fs.readFileSync('/tmp/stylecost.json')).traceEvents;
  const sum = {}, add = (k, d) => { sum[k] = (sum[k] || 0) + d; };
  let frames = 0;
  for (const e of ev) {
    if (e.ph !== 'X') continue;
    const d = (e.dur || 0) / 1000;
    if (e.name === 'UpdateLayoutTree') add(`style[${e.args?.elementCount ?? '?'}]`, d);
    else if (e.name === 'Layout') add('layout', d);
    else if (e.name === 'Blink.Animate.UpdateTime') add('animate', d);
    else if (e.name === 'ProxyMain::BeginMainFrame') { add('frame', d); frames++; }
  }
  const per = Object.entries(sum).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${(v / frames).toFixed(1)}`);
  console.log(`${path.basename(file)}: ${rate.toFixed(1)} steps/s, ${(frames / 3).toFixed(1)} frames/s | ms/frame: ${per.join(', ')}`);
}
