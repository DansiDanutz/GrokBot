import { existsSync, mkdtempSync, renameSync, rmSync, unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { execFileSync } from 'node:child_process';

export function exclusionArgs(version) {
  if (/GNU tar/.test(version)) return ['--anchored', '--exclude=enterprise'];
  if (/bsdtar/.test(version)) return ['--exclude=^enterprise'];
  throw new Error('Unsupported tar implementation; OSS export requires GNU tar or bsdtar.');
}

export function extractOssArchive(archive, destination) {
  const version = execFileSync('tar', ['--version'], { encoding: 'utf8' });
  const excludes = exclusionArgs(version);
  const staging = mkdtempSync(join(dirname(destination), 'extract-'));
  try {
    execFileSync('tar', [...excludes, '-xf', archive, '-C', staging], { stdio: 'inherit' });
    if (existsSync(join(staging, 'enterprise')) || !existsSync(join(staging, 'LICENSE')) || !existsSync(join(staging, 'NOTICE'))) {
      throw new Error('OSS export validation failed');
    }
    if (existsSync(destination)) throw new Error('OSS export destination already exists');
    renameSync(staging, destination);
    // Only the exact archive just validated is ours to remove, never a glob.
    unlinkSync(archive);
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}
