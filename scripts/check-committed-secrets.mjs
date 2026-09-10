#!/usr/bin/env node

import { execFileSync } from 'node:child_process'

const trackedDrift = execFileSync('git', [
  'status',
  '--porcelain',
  '--untracked-files=no',
]).toString('utf8')

if (trackedDrift) {
  console.error('Tracked worktree changes detected; refusing to scan mutable files.')
  console.error('Commit or restore tracked changes, then scan the exact HEAD revision.')
  process.exit(1)
}

const trackedFiles = execFileSync('git', ['ls-tree', '-r', '--name-only', '-z', 'HEAD'])
  .toString('utf8')
  .split('\0')
  .filter(Boolean)

const findings = []
const jwtPattern = /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g
const supabaseSecretPattern = /sb_secret_[A-Za-z0-9_-]{20,}/g
const netlifyBuildHookPattern = /https:\/\/api\.netlify\.com\/build_hooks\/[a-f0-9]{20,}/gi
const credentialPatterns = [
  ['Netlify access token', /(?<![A-Za-z0-9_])nfp_[A-Za-z0-9_-]{20,}/g],
  ['Render API key', /rnd_[A-Za-z0-9_-]{20,}/g],
  ['OpenRouter API key', /sk-or-v1-[A-Za-z0-9_-]{20,}/g],
  ['Anthropic API key', /sk-ant-[A-Za-z0-9_-]{20,}/g],
  ['Groq API key', /gsk_[A-Za-z0-9_-]{20,}/g],
  ['xAI API key', /xai-[A-Za-z0-9_-]{20,}/g],
  ['OpenAI API key', /(?<![A-Za-z0-9_-])sk-(?!(?:or-v1|ant)-)(?:proj-)?[A-Za-z0-9_-]{20,}/g],
  ['Vercel deploy hook', /https:\/\/api\.vercel\.com\/v1\/integrations\/deploy\/[A-Za-z0-9_./-]{20,}/g],
  ['Render deploy hook', /https:\/\/api\.render\.com\/deploy\/[A-Za-z0-9_?=&.-]{20,}/g],
  ['GitHub token', /gh[pousr]_[A-Za-z0-9]{20,}/g],
  ['GitHub fine-grained token', /github_pat_[A-Za-z0-9_]{20,}/g],
  ['AWS access key', /AKIA[0-9A-Z]{16}/g],
  ['Google API key', /AIza[0-9A-Za-z_-]{30,}/g],
  ['Stripe live secret', /sk_live_[0-9A-Za-z]{20,}/g],
  ['Slack token', /xox[baprs]-[0-9A-Za-z-]{20,}/g],
]
const placeholderPattern = /example|placeholder|redacted|replace|your[_-]/i
const directMainPushPattern = /\bgit\s+push\b.*\borigin\b.*\bmain\b/

for (const file of trackedFiles) {
  let buffer
  try {
    buffer = execFileSync('git', ['show', `HEAD:${file}`], { maxBuffer: 64 * 1024 * 1024 })
  } catch {
    continue
  }
  if (buffer.includes(0)) continue

  const text = buffer.toString('utf8')
  const lineFor = (index) => text.slice(0, index).split('\n').length

  if (file.endsWith('.sh')) {
    for (const [index, line] of text.split('\n').entries()) {
      const command = line.trim()
      if (!command || command.startsWith('#') || /^(?:echo|printf)\b/.test(command)) continue
      if (directMainPushPattern.test(command)) {
        findings.push({ file, line: index + 1, kind: 'Direct main push helper' })
      }
    }
  }

  for (const match of text.matchAll(jwtPattern)) {
    try {
      const payload = JSON.parse(
        Buffer.from(match[0].split('.')[1], 'base64url').toString('utf8'),
      )
      if (payload?.role === 'service_role') {
        findings.push({ file, line: lineFor(match.index), kind: 'Supabase service-role JWT' })
      }
    } catch {
      // An undecodable JWT-like string is not enough evidence to fail this gate.
    }
  }

  for (const match of text.matchAll(supabaseSecretPattern)) {
    if (!placeholderPattern.test(match[0])) {
      findings.push({ file, line: lineFor(match.index), kind: 'Supabase secret key' })
    }
  }

  for (const match of text.matchAll(netlifyBuildHookPattern)) {
    findings.push({ file, line: lineFor(match.index), kind: 'Netlify build hook' })
  }

  for (const [kind, pattern] of credentialPatterns) {
    for (const match of text.matchAll(pattern)) {
      if (!placeholderPattern.test(match[0])) {
        findings.push({ file, line: lineFor(match.index), kind })
      }
    }
  }
}

if (findings.length) {
  console.error('Committed security policy violations detected:')
  for (const finding of findings) {
    console.error(`- ${finding.file}:${finding.line} (${finding.kind})`)
  }
  console.error('Remove blocked content; rotate exposed credentials and purge Git history where required.')
  process.exit(1)
}

console.log(`Committed-secret gate passed (${trackedFiles.length} tracked files scanned).`)
