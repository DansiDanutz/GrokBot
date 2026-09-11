import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { paths, settings, initialize, runtimeEnv } from './runtime.mjs';
import { superviseChild, waitForReady } from './lifecycle.mjs';
import { acquireServerLease } from './server-process.mjs';
import { attachServerLogs } from './server-logs.mjs';
import { HEALTH_TIMEOUT_MS } from './lifecycle-constants.mjs';

async function assertAvailable(port) {
  await new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', () => reject(new Error(`Port ${port} is occupied. Stop only your GrokBot instance or choose another port in config/workspace.json.`)));
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}
let lease;
try {
  const p = paths(), s = settings();
  if (!existsSync(join(p.upstream, 'dist/index.html')) || !existsSync(p.codex)) throw new Error('Run npm ci and npm run setup first.');
  const entrypoint = join(p.upstream, 'server/index.ts');
  lease = acquireServerLease(p.runtime, entrypoint);
  await assertAvailable(s.port); await assertAvailable(s.port + 1);
  initialize(p, s);
  const child = spawn(process.execPath, ['--experimental-strip-types', entrypoint], { cwd: p.upstream, env: runtimeEnv(p, s), stdio: ['ignore', 'pipe', 'pipe'], detached: true });
  const lifecycle = superviseChild(child);
  attachServerLogs(child, p.runtime, () => { lifecycle.fail(); lifecycle.stop(); console.error('Server log failure; stopping this instance.'); });
  child.once('error', error => console.error(error.message));
  let startupFailed = false;
  try {
    await new Promise((resolve, reject) => {
      child.once('spawn', () => { try { lease.record(child.pid); resolve(); } catch (error) { reject(error); } });
      child.once('error', reject);
    });
    const ready = await waitForReady(lifecycle, async (signal) => {
      const response = await fetch(`http://127.0.0.1:${s.port}/api/health`, { signal: AbortSignal.any([signal, AbortSignal.timeout(HEALTH_TIMEOUT_MS)]) });
      const body = await response.json();
      return response.ok && body.app === 'openmausbot' && body.pid === child.pid;
    });
    if (ready) console.log(`GrokBot workspace ready: http://127.0.0.1:${s.port} (upstream OpenMausBot OSS UI). Ctrl-C stops this instance.`);
  } catch (error) {
    if (!lifecycle.stopping) {
      startupFailed = true;
      lifecycle.fail();
      console.error(error.message);
    }
    lifecycle.stop();
  }
  const childStatus = await lifecycle.done;
  process.exitCode = startupFailed ? 1 : childStatus;
} catch (error) { console.error(error.message); process.exitCode = 1; }
finally { if (lease) lease.release(); }
