import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { ROOT, paths, settings } from './runtime.mjs';
for (const directory of ['scripts', 'tests']) {
  for (const file of readdirSync(join(ROOT, directory)).filter(f => f.endsWith('.mjs'))) execFileSync(process.execPath, ['--check', join(ROOT, directory, file)], { stdio: 'inherit' });
}
for (const file of ['paper_grid/dashboard.html', 'paper_grid/public/index.html']) {
  const html = readFileSync(join(ROOT, file), 'utf8');
  for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
    execFileSync(process.execPath, ['--check', '-'], { input: script[1], stdio: ['pipe', 'inherit', 'inherit'] });
  }
}
paths(); settings();
console.log('Syntax and configuration checks passed.');
