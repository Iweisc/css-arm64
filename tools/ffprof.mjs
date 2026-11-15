// Dev-only: runs a page in Firefox with the Gecko profiler on and dumps the profile at exit.
import { firefox } from 'playwright';
import path from 'node:path';
const b = await firefox.launch({ env: { ...process.env, MOZ_PROFILER_STARTUP: '1', MOZ_PROFILER_SHUTDOWN: '/tmp/ffprof.json',
  MOZ_PROFILER_STARTUP_FEATURES: 'js,stackwalk,cpu', MOZ_PROFILER_STARTUP_INTERVAL: '1' } });
const p = await b.newPage();
await p.goto('file://' + path.resolve(process.argv[2]));
await p.waitForTimeout(+(process.argv[3] || 8000));
await b.close();
