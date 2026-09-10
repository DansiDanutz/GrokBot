import { mkdirSync, existsSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { paths, settings, initialize } from './runtime.mjs';

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit', env: { ...process.env, NODE_OPTIONS: process.env.NODE_OPTIONS ?? '--max-old-space-size=1024', ELECTRON_SKIP_BINARY_DOWNLOAD: '1' } });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (${result.status})`);
}
try {
  const p = paths(), s = settings();
  mkdirSync(p.runtime, { recursive: true, mode: 0o700 });
  if (!existsSync(p.codex)) throw new Error('Run npm ci first to install the project-local Codex CLI.');
  if (!existsSync(p.upstream)) {
    if (!existsSync(join(p.source, '.git'))) run('git', ['clone', '--filter=blob:none', '--no-checkout', p.pin.repository, p.source], p.root);
    run('git', ['fetch', '--depth=1', 'origin', p.pin.commit], p.source);
    const archive = join(p.runtime, `${p.pin.commit}.tar`);
    run('git', ['archive', '--format=tar', `--output=${archive}`, p.pin.commit], p.source);
    const staging = join(p.runtime, `extract-${Date.now()}`);
    mkdirSync(staging, { mode: 0o700 });
    run('tar', ['-xf', archive, '--exclude=enterprise', '-C', staging], p.root);
    if (existsSync(join(staging, 'enterprise')) || !existsSync(join(staging, 'LICENSE')) || !existsSync(join(staging, 'NOTICE'))) throw new Error('OSS export validation failed');
    renameSync(staging, p.upstream);
  }
  const pnpm = join(p.root, 'node_modules/pnpm/bin/pnpm.cjs');
  run(process.execPath, [pnpm, 'install', '--frozen-lockfile', '--ignore-scripts'], p.upstream);
  run(process.execPath, [pnpm, 'build'], p.upstream);
  initialize(p, s);
  console.log('Pinned OSS runtime prepared. Run npm run doctor, then npm start. No model request was submitted.');
} catch (error) { console.error(error.message); process.exitCode = 1; }
