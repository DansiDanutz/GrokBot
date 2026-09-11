import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtempSync, readFileSync, writeFileSync, existsSync, statSync, readdirSync, rmSync, mkdirSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { acquireServerLease, inspectServerPid, PID_FILE, LOCK_DIRECTORY } from '../scripts/server-process.mjs';
import { rotatingLog, attachServerLogs } from '../scripts/server-logs.mjs';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'grok-process-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}
const entrypoint = '/fixture/server/index.ts';

test('spawn lease records PID, blocks concurrent starts, removes only its owned PID on exit', (t) => {
  const root = fixture(t);
  const lease = acquireServerLease(root, entrypoint, { processCommand: () => null });
  lease.record(123);
  assert.equal(JSON.parse(readFileSync(join(root, PID_FILE))).pid, 123);
  assert.equal(statSync(join(root, PID_FILE)).mode & 0o777, 0o600);
  assert.throws(() => acquireServerLease(root, entrypoint), /start lock/);
  assert.deepEqual(inspectServerPid(root, { entrypoint, processCommand: () => `node --experimental-strip-types ${entrypoint}` }), { state: 'running', pid: 123 });
  lease.release();
  assert.equal(existsSync(join(root, PID_FILE)), false);
  assert.equal(existsSync(join(root, LOCK_DIRECTORY)), false);
});

test('PID reuse, uncertain identity and malformed records fail closed without signaling processes', (t) => {
  const root = fixture(t);
  writeFileSync(join(root, PID_FILE), JSON.stringify({ pid: 123, entrypoint }));
  const options = { processCommand: () => 'node /different/server.js' };
  assert.deepEqual(inspectServerPid(root, { entrypoint, ...options }), { state: 'mismatch', pid: 123 });
  assert.throws(() => acquireServerLease(root, entrypoint, options), /mismatch/);
  assert.equal(existsSync(join(root, LOCK_DIRECTORY)), false);
  assert.deepEqual(inspectServerPid(root, { entrypoint, processCommand: () => { throw Error('private path'); } }), { state: 'unknown', pid: 123 });
  writeFileSync(join(root, PID_FILE), '{');
  assert.deepEqual(inspectServerPid(root, { entrypoint }), { state: 'invalid' });
  assert.throws(() => acquireServerLease(root, entrypoint), /invalid/);
});

test('dead PID can be replaced under the lease, with unrelated replacement preserved on release', (t) => {
  const root = fixture(t);
  writeFileSync(join(root, PID_FILE), JSON.stringify({ pid: 123, entrypoint }));
  assert.deepEqual(inspectServerPid(root, { entrypoint, processCommand: () => null }), { state: 'stale', pid: 123 });
  const lease = acquireServerLease(root, entrypoint, { processCommand: () => null });
  lease.record(456);
  writeFileSync(join(root, PID_FILE), JSON.stringify({ pid: 789, entrypoint }));
  lease.release();
  assert.equal(JSON.parse(readFileSync(join(root, PID_FILE))).pid, 789);
});

test('rotating logs bound every file and archive count even with oversized chunks', (t) => {
  const root = fixture(t), file = join(root, 'logs/server.stdout.log');
  const log = rotatingLog(file, { maxBytes: 8, backups: 2 });
  log.write(Buffer.from('abcdefghijklmnopqrstuvwxyz1234'));
  log.close();
  assert.deepEqual(readdirSync(join(root, 'logs')).sort(), ['server.stdout.log', 'server.stdout.log.1', 'server.stdout.log.2']);
  for (const name of readdirSync(join(root, 'logs'))) {
    assert.ok(statSync(join(root, 'logs', name)).size <= 8);
    assert.equal(statSync(join(root, 'logs', name)).mode & 0o777, 0o600);
  }
  assert.equal(statSync(join(root, 'logs')).mode & 0o777, 0o700);
  assert.equal(readFileSync(`${file}.2`, 'utf8') + readFileSync(`${file}.1`, 'utf8') + readFileSync(file, 'utf8'), 'ijklmnopqrstuvwxyz1234');
});

test('server stdout and stderr are written separately and stream errors fail supervision', (t) => {
  const root = fixture(t), child = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough();
  let failed = 0;
  attachServerLogs(child, root, () => failed++, { maxBytes: 8, backups: 1 });
  child.stdout.write('out'); child.stderr.write('err');
  child.stdout.emit('error', Error('private details'));
  child.emit('close');
  assert.equal(failed, 1);
  assert.equal(readFileSync(join(root, 'logs/server.stdout.log'), 'utf8'), 'out');
  assert.equal(readFileSync(join(root, 'logs/server.stderr.log'), 'utf8'), 'err');
});

test('existing oversized logs and archives are bounded on reopen, retaining their tails', (t) => {
  const root = fixture(t), file = join(root, 'logs/server.stdout.log');
  mkdirSync(join(root, 'logs'));
  for (const name of [file, `${file}.1`, `${file}.2`]) writeFileSync(name, '0123456789ABCDEF');
  const log = rotatingLog(file, { maxBytes: 8, backups: 2 });
  log.close();
  for (const name of [file, `${file}.1`, `${file}.2`]) {
    assert.equal(readFileSync(name, 'utf8'), '89ABCDEF');
    assert.ok(statSync(name).size <= 8);
  }
});

test('symlinked log directory, active file or rotation backup is rejected without target changes', (t) => {
  for (const location of ['directory', 'active', 'backup']) {
    const root = fixture(t), target = join(root, 'unrelated');
    mkdirSync(target); writeFileSync(join(target, 'keep'), 'untouched');
    const file = join(root, 'logs/server.stdout.log');
    if (location === 'directory') symlinkSync(target, join(root, 'logs'));
    else {
      mkdirSync(join(root, 'logs'));
      symlinkSync(join(target, 'keep'), location === 'active' ? file : `${file}.1`);
    }
    const originalMode = statSync(target).mode;
    assert.throws(() => rotatingLog(file, { maxBytes: 8, backups: 2 }), /Unsafe log path/);
    assert.equal(statSync(target).mode, originalMode);
    assert.equal(readFileSync(join(target, 'keep'), 'utf8'), 'untouched');
  }
});
