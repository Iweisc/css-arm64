// Dev-only: loads index.html, leaves it alone for N seconds, then screenshots it.
// Usage: node tools/shot.mjs engine seconds out.png   (PAGE=other.html to load another file)
import { chromium, firefox } from 'playwright';
import path from 'node:path';
const engines = { chromium, firefox };
const [name, secs, out] = [process.argv[2], +process.argv[3], process.argv[4]];
const b = await engines[name].launch();
const p = await b.newPage({ viewport: { width: 1260, height: 720 } });
await p.goto('file://' + path.resolve(process.env.PAGE || 'index.html'));
await p.waitForTimeout(secs * 1000);
await p.screenshot({ path: out });
await b.close();
