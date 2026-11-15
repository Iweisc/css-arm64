// Dev-only: loads index.html, samples CPU state over time, and prints the terminal as the cells hold it.
// Usage: node tools/run.mjs [seconds] [typed text]   (ENGINES=chromium,firefox)
// UNTIL=text stops once the screen shows it and prints the time; TICK=ms sets the sampling (and typing) interval.
import { chromium, firefox } from 'playwright';
import path from 'node:path';

const engines = { chromium, firefox };
const secs = +(process.argv[2] || 20);
const typed = (process.argv[3] || '').replace(/\\n/g, '\n');
const page = 'file://' + path.resolve(process.env.PAGE || 'index.html');

function sample() {
  const st = getComputedStyle(document.querySelector('.st'));
  const g = (n) => +st.getPropertyValue('--S_' + n);
  const rows = [...document.querySelectorAll('.trow')].map((r) => {
    // Each cell holds four characters, two 7-bit codes per axis (see cssgen.page).
    const cells = [...r.querySelectorAll('.tc')].map((c) => {
      const cs = getComputedStyle(c);
      return [+cs.getPropertyValue('--ta'), +cs.getPropertyValue('--tb')]
        .flatMap((v) => [v % 128, Math.floor(v / 128)])
        .map((ch) => String.fromCharCode(ch >= 32 && ch < 127 ? ch : 32)).join('');
    });
    return [+getComputedStyle(r).getPropertyValue('--vy'), cells.join('').trimEnd()];
  });
  rows.sort((a, b) => a[0] - b[0]);
  return {
    pc: g('pc0') + g('pc1') * 65536, cnt: g('cnt0') + g('cnt1') * 65536, phase: g('phase'), el: g('el'),
    x0: g('x0_0'), sp: g('sp1_0'), screen: rows.map((r) => r[1]).join('\n'),
  };
}

for (const name of (process.env.ENGINES || 'chromium,firefox').split(',')) {
  const b = await engines[name].launch();
  const p = await b.newPage({ viewport: { width: 1260, height: 720 } });
  p.on('pageerror', (e) => console.log('pageerror', e.message));
  await p.goto(page);
  const t0 = Date.now();
  let last = await p.evaluate(sample), lastT = t0;
  const keys = [...typed];
  let lineStart = true;
  while (Date.now() - t0 < secs * 1000) {
    await p.waitForTimeout(+(process.env.TICK || 2000));
    const s = await p.evaluate(sample);
    const now = Date.now();
    console.log(`${name} t=${((now - t0) / 1000).toFixed(0)}s pc=${s.pc.toString(16)} steps=${s.cnt} ` +
      `rate=${((s.cnt - last.cnt) / ((now - lastT) / 1000)).toFixed(1)}/s el=${s.el} sp=${s.sp.toString(16)}`);
    last = s; lastT = now;
    if (process.env.UNTIL && s.screen.includes(process.env.UNTIL)) {
      console.log(`${name} ${path.basename(page)}: "${process.env.UNTIL}" after ${((now - t0) / 1000).toFixed(1)} s, ${s.cnt} steps`);
      break;
    }
    // Start each line at a fresh prompt, as a person would.
    const atPrompt = s.screen.trimEnd().split('\n').pop() === '$';
    if (keys.length && (!lineStart || atPrompt)) {
      const ch = keys.shift();
      lineStart = ch === '\n';
      const sel = `.kb${ch.charCodeAt(0)}`;
      await p.hover(sel); await p.mouse.down(); await p.waitForTimeout(150); await p.mouse.up();
    }
  }
  console.log(last.screen);
  await p.screenshot({ path: `/tmp/run-${name}.png` });
  console.log(`screenshot /tmp/run-${name}.png`);
  await b.close();
}
