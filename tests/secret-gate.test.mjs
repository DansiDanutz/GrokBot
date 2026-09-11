import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const scanner = fileURLToPath(new URL('../scripts/check-committed-secrets.mjs', import.meta.url));
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'grok-secret-gate-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' });
  git('init'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'user.name', 'Fixture');
  writeFileSync(join(root, 'safe.txt'), 'initial\n'); git('add', '.'); git('commit', '-m', 'fixture');
  return { root, git, scan: () => spawnSync(process.execPath, [scanner], { cwd: root, encoding: 'utf8' }) };
}
test('pre-commit gate scans the staged snapshot including new files', t => {
  const { root, git, scan } = fixture(t);
  writeFileSync(join(root, 'safe.txt'), 'updated\n');
  writeFileSync(join(root, 'new.txt'), 'safe\n'); git('add', '.');
  const result = scan(); assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /staged.*2.*files/i);
});
test('staged credential-shaped fixtures are blocked without echoing values', t => {
  const { root, git, scan } = fixture(t);
  const fake = 'ghp_' + 'Z'.repeat(30);
  writeFileSync(join(root, 'new.txt'), fake); git('add', '.');
  const result = scan(); assert.equal(result.status, 1);
  assert.match(result.stderr, /GitHub token/); assert.ok(!result.stderr.includes(fake));
});
test('unstaged tracked changes fail the gate rather than scanning stale bytes', t => {
  const { root, scan } = fixture(t);
  writeFileSync(join(root, 'safe.txt'), 'changed\n');
  assert.equal(scan().status, 1);
});
