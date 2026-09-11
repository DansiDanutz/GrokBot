import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('source lock distinguishes development from the immutable v1 import', () => {
  const lock = JSON.parse(readFileSync(new URL('../config/paper-source.lock.json', import.meta.url)));
  assert.equal(lock.development_source.repository, 'https://github.com/DansiDanutz/GrokBot');
  assert.equal(lock.v1_control.frozen, true);
  assert.equal(lock.v1_control.baseline_commit, lock.source_commit);
  assert.match(lock.scope, /historical/i);
  assert.ok(lock.v1_control.telemetry_followup_commit);
});
