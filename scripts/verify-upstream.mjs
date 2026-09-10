import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { paths } from './runtime.mjs';
const p = paths();
const env = { ...process.env, NODE_OPTIONS: '--max-old-space-size=1024' };
// Upstream's tests create their own HOME, but an inherited CODEX_HOME can
// otherwise override it and make the fake catalog read this app's account.
for (const key of ['CODEX_HOME', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'XAI_API_KEY', 'OMB_DATA_DIR', 'HERMES_HOME']) delete env[key];
// The parent repository ignores .runtime/, which makes upstream's plain
// `oxlint .` skip every file. Enumerate the pinned tracked source instead,
// retaining its excluded generated/vendor directories without linting deps.
const listing = spawnSync('git', ['ls-tree', '-r', '--name-only', p.pin.commit], { cwd: p.source, encoding: 'utf8' });
if (listing.status !== 0) throw new Error('Cannot enumerate pinned upstream source');
const lintFiles = listing.stdout.trim().split('\n').filter(file => /\.(?:[cm]?[jt]s|[jt]sx)$/.test(file)
  && !/^(?:enterprise|third_party|dist[^/]*|release|\.[^/]+)\//.test(file)
  && !/^electron\/(?:vendor|resources)\//.test(file));
const checks = [
  ['exec', 'oxlint', '--no-ignore', '--deny-warnings', '--threads=2', ...lintFiles], ['typecheck'],
  ['exec', 'vitest', 'run', 'server/config.test.ts', 'server/default-model-selection.test.ts', 'server/mcp-registry.test.ts', 'server/enterprise.test.ts', 'server/drivers/codex.test.ts', 'server/drivers/codex-catalog.test.ts', 'server/drivers/codex-instructions.test.ts'],
];
for (const args of checks) {
  const result = spawnSync(process.execPath, [join(p.root, 'node_modules/pnpm/bin/pnpm.cjs'), ...args], { cwd: p.upstream, env, stdio: 'inherit' });
  if (result.error || result.status !== 0) { console.error(`Upstream ${args[0]} failed`); process.exit(1); }
}
