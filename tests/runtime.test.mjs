import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, statSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { paths, settings, initialConfig, initialize, runtimeEnv } from '../scripts/runtime.mjs';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'grokbot-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'config'));
  writeFileSync(join(root, 'upstream.lock.json'), JSON.stringify({ repository: 'https://github.com/milind-soni/OpenMausBot.git', commit: 'a'.repeat(40), edition: 'oss' }));
  writeFileSync(join(root, 'config/workspace.json'), JSON.stringify({ name: 'Fixture', model: 'gpt-6-astra', effort: 'low', port: 8799, maxConcurrentPerBot: 2, localVmMaxInstances: 1 }));
  return { root, p: paths(root), s: settings(root) };
}
test('initial configuration pins Astra and subscription CLI with bounded concurrency', t => {
  const { p, s } = fixture(t), cfg = initialConfig(p, s);
  assert.deepEqual(cfg.defaultModelSelection, { instanceId: 'codex', model: 'gpt-6-astra', effort: 'low' });
  assert.equal(cfg.instances.codex.config.fullAuto, false);
  assert.equal(cfg.threads.maxConcurrentPerBot, 2);
  assert.equal(cfg.cliStartup.access, 'local');
  assert.equal(cfg.mcpServers.danslab_status.command, '/opt/homebrew/bin/node');
});
test('setup creates private state and never overwrites app-owned configuration', t => {
  const { p, s } = fixture(t), file = initialize(p, s);
  assert.equal(statSync(file).mode & 0o777, 0o600);
  writeFileSync(file, '{"userPreference":true}');
  initialize(p, s);
  assert.equal(readFileSync(file, 'utf8'), '{"userPreference":true}');
});
test('child environment strips credentials, tunnel overrides and inherited runtime paths', t => {
  const { p, s } = fixture(t);
  const env = runtimeEnv(p, s, { HOME: '/fixture/home', PATH: '/bin', OPENAI_API_KEY: 'fixture-secret', ANTHROPIC_API_KEY: 'fixture-secret', OMB_PUBLIC_URL: 'https://invalid.example', OMB_DATA_DIR: '/other/state' });
  assert.equal(env.HOME, '/fixture/home');
  assert.equal(env.OMB_DATA_DIR, p.data);
  assert.equal(env.OMB_PORT, '8799');
  for (const key of ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OMB_PUBLIC_URL']) assert.equal(key in env, false);
});
test('invalid model effort and unbounded port are rejected', t => {
  const { root, s } = fixture(t);
  for (const change of [{ effort: 'none' }, { port: 65535 }, { port: '8799' }, { maxConcurrentPerBot: 10 }, { model: 'gpt-5' }]) {
    writeFileSync(join(root, 'config/workspace.json'), JSON.stringify({ ...s, ...change }));
    assert.throws(() => settings(root), /Invalid/);
  }
});

test('doctor detects missing or versioned MCP command without rewriting config', async t => {
  const { inspectMcpCommand } = await import('../scripts/doctor-config.mjs');
  const { p, s } = fixture(t), file = initialize(p, s);
  const config = initialConfig(p, s);
  config.mcpServers.danslab_status.command = '/opt/homebrew/Cellar/node/24.0.0/bin/node';
  writeFileSync(file, JSON.stringify(config));
  const before = readFileSync(file, 'utf8');
  const report = inspectMcpCommand(p, () => false);
  assert.equal(report.commandExists, false);
  assert.equal(report.stableCommand, false);
  assert.match(report.warning, /stable/);
  assert.match(report.fix, /mcpServers/);
  assert.match(report.fix, /opt\/homebrew\/bin\/node/);
  assert.equal(report.fix.split('\n').length, 1);
  assert.equal(readFileSync(file, 'utf8'), before);
  config.mcpServers.danslab_status.command = '/opt/homebrew/bin/node';
  writeFileSync(file, JSON.stringify(config));
  assert.equal(inspectMcpCommand(p, () => true).warning, null);
  assert.match(inspectMcpCommand(p, () => false).warning, /missing/);
});
