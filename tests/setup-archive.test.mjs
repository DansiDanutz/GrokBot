import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readFileSync, statSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import { extractOssArchive, exclusionArgs } from '../scripts/archive.mjs';

function fixture(t, valid = true) {
  const root = mkdtempSync(join(tmpdir(), 'grok-archive-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, 'source');
  for (const name of ['enterprise', 'enterprise-tools', 'docs/enterprise']) mkdirSync(join(source, name), { recursive: true });
  for (const name of ['enterprise/private.txt', 'enterprise-notes.txt', 'enterprise-tools/public.txt', 'docs/enterprise/public.txt', 'LICENSE', ...(valid ? ['NOTICE'] : [])]) writeFileSync(join(source, name), name);
  const archive = join(root, 'pin.tar');
  execFileSync('tar', ['-cf', archive, '-C', source, 'enterprise', 'enterprise-notes.txt', 'enterprise-tools', 'docs', 'LICENSE', ...(valid ? ['NOTICE'] : [])]);
  return { root, archive, destination: join(root, 'export') };
}

test('tar exclusion is explicitly root anchored for GNU and BSD dialects', () => {
  assert.deepEqual(exclusionArgs('tar (GNU tar) 1.35'), ['--anchored', '--exclude=enterprise']);
  assert.deepEqual(exclusionArgs('bsdtar 3.5.3 - libarchive'), ['--exclude=^enterprise']);
  assert.throws(() => exclusionArgs('unknown tar'), /Unsupported tar/);
});

test('real tar excludes root enterprise, retains nested enterprise, deletes only successful pin archive', (t) => {
  const { root, archive, destination } = fixture(t);
  const bytes = statSync(archive).size;
  writeFileSync(join(root, 'unrelated.tar'), 'retain');
  extractOssArchive(archive, destination);
  assert.equal(existsSync(join(destination, 'enterprise')), false);
  assert.equal(readFileSync(join(destination, 'docs/enterprise/public.txt'), 'utf8'), 'docs/enterprise/public.txt');
  assert.equal(readFileSync(join(destination, 'enterprise-notes.txt'), 'utf8'), 'enterprise-notes.txt');
  assert.equal(readFileSync(join(destination, 'enterprise-tools/public.txt'), 'utf8'), 'enterprise-tools/public.txt');
  assert.equal(existsSync(archive), false);
  assert.equal(readFileSync(join(root, 'unrelated.tar'), 'utf8'), 'retain');
  console.log(`Fixture archive reclaimed exactly ${bytes} bytes; unrelated tar retained.`);
});

test('failed export validation retains archive for diagnosis', (t) => {
  const { archive, destination } = fixture(t, false);
  assert.throws(() => extractOssArchive(archive, destination), /OSS export validation failed/);
  assert.equal(existsSync(archive), true);
  assert.equal(existsSync(destination), false);
});
