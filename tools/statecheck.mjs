// Dev-only: the browser half of tools/statecheck.py. Reads cases from stdin, puts each one's
// starting state on the paused page's root and prints the next state the last issue slot computes.
import * as pw from 'playwright';
import path from 'node:path';
import fs from 'node:fs';

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const b = await pw[process.argv[2] || 'chromium'].launch();
const p = await b.newPage({ viewport: { width: 1260, height: 720 } });
await p.goto('file://' + path.resolve('index.html'));
await p.waitForTimeout(1500);
const out = await p.evaluate(([cases, dump]) => {
  document.getAnimations().forEach((a) => a.pause());
  const root = document.querySelector('.cpu');
  // The rendered subjects belong to the last slot this engine runs.
  const sc = [...document.querySelectorAll('.sw>.sc')].filter((e) => [...e.children].some((c) => c.matches('i') && c.getClientRects().length > 0)).pop();
  return cases.map((c) => {
    for (const [n, v] of Object.entries(c.S)) root.style.setProperty('--LS_' + n, v, 'important');
    for (const [n, v] of Object.entries(c.R)) root.style.setProperty('--L' + n, v, 'important');
    root.style.setProperty('--key', 0, 'important');
    document.body.offsetTop;  // Firefox's getComputedStyle doesn't lay out, and containers need it
    // DUMP=a,b,... also returns those values from every slot's .ex, to see where slots part.
    if (dump.length) return Object.fromEntries([...document.querySelectorAll('.ex')].map((ex, i) =>
      [i, dump.map((n) => n + '=' + getComputedStyle(ex).getPropertyValue('--' + n).trim()).join(' ')]));
    const cs = getComputedStyle(sc);
    return Object.fromEntries(Object.keys(c.N).map((n) => [n, +cs.getPropertyValue('--N_' + n)]));
  });
}, [cases, (process.env.DUMP || '').split(',').filter(Boolean)]);
console.log(JSON.stringify(out));
await b.close();
