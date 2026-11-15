// Dev-only: capture a Chrome trace for a page and print the heaviest event names by self-ish total duration.
import { chromium } from 'playwright';
import path from 'node:path';
import fs from 'node:fs';
const b = await chromium.launch();
const p = await b.newPage();
// CSS=... adds a style sheet before the page runs (for experiments).
if (process.env.CSS) await p.addInitScript((css) => document.addEventListener('DOMContentLoaded', () => { const st = document.createElement('style'); st.textContent = css; document.head.append(st); }), process.env.CSS);
await p.goto('file://' + path.resolve(process.argv[2]));
await p.waitForTimeout(+(process.env.WAIT || 1000));
await b.startTracing(p, { path: '/tmp/trace.json', categories: (process.env.CATS || 'blink,devtools.timeline,disabled-by-default-devtools.timeline,cc,blink.animations').split(',') });
await p.waitForTimeout(2000);
await b.stopTracing();
await b.close();
const ev = JSON.parse(fs.readFileSync('/tmp/trace.json')).traceEvents.filter(e => e.ph === 'X' && e.dur);
const tot = {};
for (const e of ev) tot[e.name] = (tot[e.name] || 0) + e.dur;
Object.entries(tot).sort((a, b) => b[1] - a[1]).slice(0, +(process.env.TOP || 25)).forEach(([k, v]) => console.log((v / 1000).toFixed(0).padStart(7), 'ms', k));
