import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { ROOT, paths, settings } from './runtime.mjs';
for (const directory of ['scripts', 'tests']) {
  for (const file of readdirSync(join(ROOT, directory)).filter(f => f.endsWith('.mjs'))) execFileSync(process.execPath, ['--check', join(ROOT, directory, file)], { stdio: 'inherit' });
}
paths(); settings();
console.log('Syntax and configuration checks passed.');
