import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import { PID_FILE, LOCK_DIRECTORY } from '../scripts/server-process.mjs';

async function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'grok-server-integration-')), pin = 'a'.repeat(40);
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const path of ['scripts', 'config', 'node_modules/.bin', `.runtime/${pin}/server`, `.runtime/${pin}/dist`]) mkdirSync(join(root, path), { recursive: true });
  for (const name of ['start.mjs', 'runtime.mjs', 'lifecycle.mjs', 'lifecycle-constants.mjs', 'server-process.mjs', 'server-logs.mjs', 'doctor.mjs', 'doctor-config.mjs']) {
    copyFileSync(new URL(`../scripts/${name}`, import.meta.url), join(root, 'scripts', name));
  }
  const reservation = createServer(); reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  writeFileSync(join(root, 'upstream.lock.json'), JSON.stringify({ repository: 'https://github.com/milind-soni/OpenMausBot.git', commit: pin, edition: 'oss' }));
  writeFileSync(join(root, 'config/workspace.json'), JSON.stringify({ name: 'Integration fixture', model: 'gpt-6-astra', effort: 'low', port, maxConcurrentPerBot: 1, localVmMaxInstances: 1 }));
  writeFileSync(join(root, 'node_modules/.bin/codex'), '#!/bin/sh\nprintf "ChatGPT fixture\\n"\n', { mode: 0o700 });
  writeFileSync(join(root, `.runtime/${pin}/dist/index.html`), 'fixture');
  writeFileSync(join(root, `.runtime/${pin}/server/index.ts`), `
    const http = require('node:http');
    console.log('fixture stdout'); console.error('fixture stderr');
    http.createServer((request, response) => {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({app:'openmausbot',pid:process.pid}));
    }).listen(Number(process.env.OMB_PORT),'127.0.0.1');
  `);
  return root;
}

test('isolated launcher owns PID, doctor verifies identity, stdio logs persist and shutdown cleans ownership', { timeout: 10000 }, async (t) => {
  const root = await fixture(t), runtime = join(root, '.runtime');
  const launcher = spawn(process.execPath, [join(root, 'scripts/start.mjs')], { stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(() => launcher.kill('SIGTERM'));
  let output = '';
  while (!output.includes('workspace ready:')) output += (await once(launcher.stdout, 'data', { signal: AbortSignal.timeout(5000) }))[0];
  const pid = JSON.parse(readFileSync(join(runtime, PID_FILE), 'utf8')).pid;
  const doctor = spawnSync(process.execPath, [join(root, 'scripts/doctor.mjs')], { encoding: 'utf8', timeout: 5000 });
  assert.equal(doctor.status, 0, doctor.stderr);
  assert.deepEqual(JSON.parse(doctor.stdout).serverProcess, { state: 'running', pid });
  assert.equal(existsSync(join(runtime, LOCK_DIRECTORY)), true);
  launcher.kill('SIGTERM');
  assert.equal((await once(launcher, 'close'))[0], 0);
  assert.equal(existsSync(join(runtime, PID_FILE)), false);
  assert.equal(existsSync(join(runtime, LOCK_DIRECTORY)), false);
  assert.match(readFileSync(join(runtime, 'logs/server.stdout.log'), 'utf8'), /fixture stdout/);
  assert.match(readFileSync(join(runtime, 'logs/server.stderr.log'), 'utf8'), /fixture stderr/);
});
