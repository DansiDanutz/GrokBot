import { readFileSync, mkdirSync, existsSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const STABLE_NODE = '/opt/homebrew/bin/node';
export function paths(root = ROOT) {
  const runtime = join(root, '.runtime');
  const pin = JSON.parse(readFileSync(join(root, 'upstream.lock.json'), 'utf8'));
  if (!/^[a-f0-9]{40}$/.test(pin.commit) || pin.repository !== 'https://github.com/milind-soni/OpenMausBot.git' || pin.edition !== 'oss') throw new Error('Invalid upstream pin');
  return { root, runtime, pin, source: join(runtime, 'source'), upstream: join(runtime, pin.commit), data: join(runtime, 'data'), codex: join(root, 'node_modules', '.bin', 'codex') };
}
export function settings(root = ROOT) {
  const value = JSON.parse(readFileSync(join(root, 'config/workspace.json'), 'utf8'));
  if (typeof value.name !== 'string' || !value.name.trim() || value.model !== 'gpt-6-astra' || !['low','medium','high','xhigh','max'].includes(value.effort)) throw new Error('Invalid workspace model configuration');
  if (!Number.isInteger(value.port) || value.port < 1024 || value.port > 65534) throw new Error('Invalid port');
  if (!Number.isInteger(value.maxConcurrentPerBot) || value.maxConcurrentPerBot < 1 || value.maxConcurrentPerBot > 2 || value.localVmMaxInstances !== 1) throw new Error('Invalid Mac Studio concurrency limits');
  return value;
}
export function initialConfig(p, s) {
  return {
    profile: { name: s.name },
    defaultModelSelection: { instanceId: 'codex', model: s.model, effort: s.effort },
    cliStartup: { access: 'local' },
    threads: { maxConcurrentPerBot: s.maxConcurrentPerBot },
    localVm: { mode: 'shared', maxInstances: s.localVmMaxInstances },
    instances: { codex: { driver: 'codex', displayName: 'Codex · GPT-6 Astra', enabled: true, config: { cli: JSON.stringify(p.codex), fullAuto: false } } },
    mcpServers: { danslab_status: { command: STABLE_NODE, args: [join(p.root, 'scripts/fleet-mcp.mjs')], enabled: true } },
  };
}
export function initialize(p, s) {
  mkdirSync(p.data, { recursive: true, mode: 0o700 });
  const file = join(p.data, 'config.json');
  // Once the app owns configuration, setup must never reset user choices.
  if (!existsSync(file)) writeFileSync(file, JSON.stringify(initialConfig(p, s), null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return file;
}
export function runtimeEnv(p, s, env = process.env) {
  // A CLI subscription is the chosen provider. Ambient API keys and public
  // tunnel settings must not silently change authentication or exposure.
  const allowed = ['PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'LANG', 'LC_ALL', 'TERM', 'CODEX_HOME'];
  const out = Object.fromEntries(allowed.filter(k => env[k] !== undefined).map(k => [k, env[k]]));
  return { ...out, TZ: 'Europe/Bucharest', OMB_DATA_DIR: p.data, OMB_PORT: String(s.port), OMB_WEBHOOK_PORT: String(s.port + 1), OMB_STATIC_DIR: join(p.upstream, 'dist'), OMB_ENTERPRISE_DIR: join(p.runtime, 'disabled-enterprise') };
}
