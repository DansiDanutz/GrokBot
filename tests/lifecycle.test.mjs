import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import { superviseChild, waitForReady } from '../scripts/lifecycle.mjs';

async function childFixture(t, source) {
  const child = spawn(process.execPath, ['-e', source], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(() => {
    if (child.exitCode === null && child.signalCode === null) {
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already exited */ }
    }
  });
  const lifecycle = superviseChild(child, { graceMs: 80, signals: new EventEmitter() });
  await once(child.stdout, 'data');
  return { child, lifecycle };
}

test('readiness failure remains unsuccessful when child handles SIGTERM with exit zero', { timeout: 3000 }, async (t) => {
  const { lifecycle } = await childFixture(t, "process.on('SIGTERM',()=>process.exit(0)); console.log('ready'); setInterval(()=>{},1000);");
  await assert.rejects(waitForReady(lifecycle, async () => false, { timeoutMs: 30, pollMs: 5 }), /did not become ready/);
  lifecycle.fail();
  lifecycle.stop();
  assert.equal(await lifecycle.done, 1);
});

test('unexpected signal termination is unsuccessful', { timeout: 3000 }, async (t) => {
  const { child, lifecycle } = await childFixture(t, "console.log('ready'); setInterval(()=>{},1000);");
  process.kill(-child.pid, 'SIGTERM');
  assert.equal(await lifecycle.done, 1);
});

test('readiness uses a wall-clock deadline even if the health check never settles', { timeout: 3000 }, async () => {
  const signals = new EventEmitter();
  const child = new EventEmitter();
  child.pid = 123456;
  const lifecycle = superviseChild(child, { signals });
  let observedSignal;
  const started = performance.now();
  await assert.rejects(waitForReady(lifecycle, (signal) => {
    observedSignal = signal;
    return new Promise(() => {});
  }, { timeoutMs: 40, pollMs: 500 }), /0.04 seconds/);
  assert.ok(performance.now() - started < 500, 'must not wait for the health check or polling interval');
  assert.equal(observedSignal.aborted, true);
  child.emit('exit', 0, null);
  await lifecycle.done;
});

test('hung child receives bounded escalation only for its owned process group', { timeout: 3000 }, async (t) => {
  const child = spawn(process.execPath, ['-e', "process.on('SIGTERM',()=>{}); console.log('ready'); setInterval(()=>{},1000);"], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(() => { try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already exited */ } });
  const calls = [];
  const signals = new EventEmitter();
  const lifecycle = superviseChild(child, {
    graceMs: 60,
    signals,
    kill(pid, signal) { calls.push([pid, signal]); process.kill(pid, signal); },
  });
  await once(child.stdout, 'data');
  signals.emit('SIGINT');
  signals.emit('SIGTERM'); // Repeated requests must not duplicate or reset escalation.
  assert.equal(await lifecycle.done, 0);
  assert.equal(child.signalCode, 'SIGKILL');
  assert.deepEqual(calls, [[-child.pid, 'SIGTERM'], [-child.pid, 'SIGKILL']]);
  assert.equal(signals.listenerCount('SIGINT'), 0);
  assert.equal(signals.listenerCount('SIGTERM'), 0);
});

test('readiness exits promptly if child exits without becoming ready', { timeout: 3000 }, async () => {
  const child = new EventEmitter();
  child.pid = 123456;
  const lifecycle = superviseChild(child, { signals: new EventEmitter() });
  const waiting = waitForReady(lifecycle, () => new Promise(() => {}));
  child.emit('exit', 0, null);
  await assert.rejects(waiting, /exited before becoming ready/);
});

test('successful readiness keeps foreground supervision until normal exit', async () => {
  const child = new EventEmitter();
  child.pid = 123456;
  const lifecycle = superviseChild(child, { signals: new EventEmitter() });
  assert.equal(await waitForReady(lifecycle, async () => true), true);
  assert.equal(lifecycle.finished, false);
  child.emit('exit', 0, null);
  assert.equal(await lifecycle.done, 0);
});


test('foreground launcher returns failure when startup child exits zero without readiness', { timeout: 5000 }, async (t) => {
  const root = mkdtempSync(join(tmpdir(), 'grokbot-lifecycle-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const pin = 'a'.repeat(40);
  for (const path of ['scripts', 'config', 'node_modules/.bin', `.runtime/${pin}/server`, `.runtime/${pin}/dist`]) mkdirSync(join(root, path), { recursive: true });
  for (const name of ['start.mjs', 'runtime.mjs', 'lifecycle.mjs', 'lifecycle-constants.mjs', 'server-process.mjs', 'server-logs.mjs']) copyFileSync(new URL(`../scripts/${name}`, import.meta.url), join(root, 'scripts', name));
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  writeFileSync(join(root, 'upstream.lock.json'), JSON.stringify({ repository: 'https://github.com/milind-soni/OpenMausBot.git', commit: pin, edition: 'oss' }));
  writeFileSync(join(root, 'config/workspace.json'), JSON.stringify({ name: 'Lifecycle fixture', model: 'gpt-6-astra', effort: 'low', port, maxConcurrentPerBot: 1, localVmMaxInstances: 1 }));
  writeFileSync(join(root, 'node_modules/.bin/codex'), 'fixture');
  writeFileSync(join(root, `.runtime/${pin}/dist/index.html`), 'fixture');
  writeFileSync(join(root, `.runtime/${pin}/server/index.ts`), 'process.exit(0);');
  const launcher = spawn(process.execPath, [join(root, 'scripts/start.mjs')], { stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(() => launcher.kill());
  let stderr = '';
  launcher.stderr.on('data', (chunk) => { stderr += chunk; });
  const [code] = await once(launcher, 'close');
  assert.equal(code, 1);
  assert.match(stderr, /exited before becoming ready/);
});
