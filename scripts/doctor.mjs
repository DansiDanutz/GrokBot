import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { paths, settings, runtimeEnv } from './runtime.mjs';
import { arch, totalmem } from 'node:os';
const p = paths(), s = settings();
const command = (args) => spawnSync(p.codex, args, { encoding: 'utf8', timeout: 15000, env: runtimeEnv(p, s) });
const version = existsSync(p.codex) ? command(['--version']) : null;
const login = version?.status === 0 ? command(['login', 'status']) : null;
const signedIn = login?.status === 0 && /ChatGPT/i.test(`${login.stdout} ${login.stderr}`);
const report = {
  architecture: arch(), memoryGiB: Math.round(totalmem() / 2 ** 30), node: process.versions.node,
  upstream: p.pin.commit, ossOnly: existsSync(p.upstream) && !existsSync(join(p.upstream, 'enterprise')),
  built: existsSync(join(p.upstream, 'dist/index.html')),
  codex: version?.status === 0 ? version.stdout.trim() : 'missing',
  authentication: signedIn ? 'ChatGPT subscription' : 'ChatGPT sign-in required',
  requestedModel: s.model, modelAccess: 'Check the live engine catalog; no generation request made by doctor.',
  localUrl: `http://127.0.0.1:${s.port}`, configurationPresent: existsSync(join(p.data, 'config.json')),
};
console.log(JSON.stringify(report, null, 2));
if (!report.built || !report.ossOnly || !signedIn) process.exitCode = 1;
