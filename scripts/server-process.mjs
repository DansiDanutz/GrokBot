import { mkdirSync, readFileSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { PROCESS_QUERY_TIMEOUT_MS, PRIVATE_DIRECTORY_MODE, PRIVATE_FILE_MODE } from './lifecycle-constants.mjs';

export const PID_FILE = 'server.pid.json';
export const LOCK_DIRECTORY = 'start.lock';

function processCommand(pid) {
  const result = spawnSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8', timeout: PROCESS_QUERY_TIMEOUT_MS });
  if (result.error) throw new Error('Process identity unavailable');
  if (result.status === 1 && !result.stdout.trim()) return null;
  if (result.status !== 0) throw new Error('Process identity unavailable');
  return result.stdout.trim();
}

export function inspectServerPid(runtime, { entrypoint, processCommand: inspect = processCommand } = {}) {
  let record;
  try { record = JSON.parse(readFileSync(join(runtime, PID_FILE), 'utf8')); } catch (error) {
    return { state: error.code === 'ENOENT' ? 'absent' : 'invalid' };
  }
  if (!Number.isSafeInteger(record?.pid) || record.pid <= 0 || record.entrypoint !== entrypoint) return { state: 'invalid' };
  const { pid } = record;
  try {
    const command = inspect(pid);
    if (command === null) return { state: 'stale', pid };
    // This is an identity check, never authority to signal a PID from disk.
    const expected = `--experimental-strip-types ${entrypoint}`;
    return { state: command.endsWith(` ${expected}`) ? 'running' : 'mismatch', pid };
  } catch { return { state: 'unknown', pid }; }
}

export function acquireServerLease(runtime, entrypoint, options = {}) {
  const lock = join(runtime, LOCK_DIRECTORY), file = join(runtime, PID_FILE);
  try { mkdirSync(lock, { mode: PRIVATE_DIRECTORY_MODE }); } catch (error) {
    if (error.code === 'EEXIST') throw new Error('Server start lock exists; another launcher may own it. Inspect before removing an orphan lock.');
    throw error;
  }
  try {
    const status = inspectServerPid(runtime, { entrypoint, ...options });
    if (!['absent', 'stale'].includes(status.state)) throw new Error(`Existing server PID is ${status.state}; no process was signaled.`);
    if (status.state === 'stale') unlinkSync(file);
  } catch (error) { rmdirSync(lock); throw error; }
  return ownedLease(file, lock, entrypoint);
}

function ownedLease(file, lock, entrypoint) {
  let ownedRecord, released = false;
  return {
    record(pid) {
      if (released || ownedRecord || !Number.isSafeInteger(pid) || pid <= 0) throw new Error('Invalid server spawn record');
      const record = JSON.stringify({ pid, entrypoint });
      writeFileSync(file, record + '\n', { flag: 'wx', mode: PRIVATE_FILE_MODE });
      ownedRecord = record;
    },
    release() {
      if (released) return;
      released = true;
      try {
        if (ownedRecord && readFileSync(file, 'utf8').trim() === ownedRecord) unlinkSync(file);
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
      finally { rmdirSync(lock); }
    },
  };
}
