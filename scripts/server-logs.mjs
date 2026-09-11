import { chmodSync, closeSync, constants, existsSync, fstatSync, ftruncateSync, lstatSync, mkdirSync, openSync, readSync, renameSync, unlinkSync, writeSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { LOG_MAX_BYTES, LOG_BACKUPS, PRIVATE_DIRECTORY_MODE, PRIVATE_FILE_MODE } from './lifecycle-constants.mjs';

function rotate(file, backups) {
  assertLogPaths(file, backups);
  if (existsSync(`${file}.${backups}`)) unlinkSync(`${file}.${backups}`);
  for (let index = backups - 1; index >= 1; index--) {
    if (existsSync(`${file}.${index}`)) renameSync(`${file}.${index}`, `${file}.${index + 1}`);
  }
  if (existsSync(file)) renameSync(file, `${file}.1`);
}

function assertLogPaths(file, backups) {
  for (const path of [dirname(file), file, ...Array.from({ length: backups }, (_, index) => `${file}.${index + 1}`)]) {
    let info;
    try { info = lstatSync(path); } catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    if (info.isSymbolicLink() || (path === dirname(file) ? !info.isDirectory() : !info.isFile())) throw new Error('Unsafe log path');
  }
}

function openBounded(file, maxBytes) {
  const descriptor = openSync(file, constants.O_RDWR | constants.O_APPEND | constants.O_CREAT | constants.O_NOFOLLOW, PRIVATE_FILE_MODE);
  try {
    chmodSync(file, PRIVATE_FILE_MODE);
    const size = fstatSync(descriptor).size;
    if (size > maxBytes) {
      const tail = Buffer.alloc(maxBytes);
      readSync(descriptor, tail, 0, maxBytes, size - maxBytes);
      ftruncateSync(descriptor, 0);
      writeSync(descriptor, tail);
    }
    return descriptor;
  } catch (error) { closeSync(descriptor); throw error; }
}

export function rotatingLog(file, { maxBytes = LOG_MAX_BYTES, backups = LOG_BACKUPS } = {}) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || !Number.isSafeInteger(backups) || backups < 1) throw new Error('Invalid log rotation bounds');
  assertLogPaths(file, backups);
  mkdirSync(dirname(file), { recursive: true, mode: PRIVATE_DIRECTORY_MODE });
  chmodSync(dirname(file), PRIVATE_DIRECTORY_MODE);
  for (let index = 1; index <= backups; index++) if (existsSync(`${file}.${index}`)) closeSync(openBounded(`${file}.${index}`, maxBytes));
  let descriptor = openBounded(file, maxBytes), size = fstatSync(descriptor).size, closed = false;
  const close = () => { if (descriptor !== null) { closeSync(descriptor); descriptor = null; } };
  return {
    write(value) {
      if (closed) return;
      const buffer = Buffer.isBuffer(value) ? value : Buffer.from(value);
      let offset = 0;
      while (offset < buffer.length) {
        if (size >= maxBytes) { close(); rotate(file, backups); descriptor = openBounded(file, maxBytes); size = 0; }
        const length = Math.min(maxBytes - size, buffer.length - offset);
        const written = writeSync(descriptor, buffer, offset, length);
        if (!written) throw new Error('Server log write failed');
        offset += written; size += written;
      }
    },
    close() { if (!closed) { closed = true; close(); } },
  };
}

export function attachServerLogs(child, runtime, fail, options) {
  const logs = [];
  let failed = false;
  const onFailure = () => { if (!failed) { failed = true; for (const log of logs) log.close(); fail(); } };
  try {
    for (const [name, stream] of [['stdout', child.stdout], ['stderr', child.stderr]]) {
      const log = rotatingLog(join(runtime, 'logs', `server.${name}.log`), options);
      logs.push(log);
      stream.on('data', data => { try { log.write(data); } catch { onFailure(); } });
      stream.on('error', onFailure);
    }
  } catch { onFailure(); }
  child.once('close', () => { for (const log of logs) log.close(); });
}
