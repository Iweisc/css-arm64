// Dev-only: steps/s of a CPU page (reads --S_cnt0 on .st). MOUSE=x,y parks the mouse there first.
import { chromium, firefox } from 'playwright';
import path from 'node:path';
const engines = { chromium, firefox };
for (const name of (process.env.ENGINES || 'chromium').split(',')) {
  const b = await engines[name].launch();
  for (const f of process.argv.slice(2)) {
    const p = await b.newPage();
    await p.goto('file://' + path.resolve(f));
    if (process.env.MOUSE) await p.mouse.move(...process.env.MOUSE.split(',').map(Number));
    const read = () => p.evaluate(() => { const s = getComputedStyle(document.querySelector('.st')); return +s.getPropertyValue('--S_cnt0') + 65536 * s.getPropertyValue('--S_cnt1'); });
    await p.waitForTimeout(2000);
    const a = await read(); const t = Date.now();
    await p.waitForTimeout(5000);
    const c = await read();
    console.log(name.padEnd(9), path.basename(f).padEnd(20), ((c - a) / ((Date.now() - t) / 1000)).toFixed(1), 'steps/s');
    await p.close();
  }
  await b.close();
}
