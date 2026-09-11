# Roadmap audit handoff

Effective after Dan's Phase 0 gate, audited at `abaf392` (PR #9).

1. Codex completes one roadmap phase on its branch, stages each task separately,
   and runs `npm run verify` plus `npm run verify:secrets` before every commit.
2. Codex publishes a PR and phase evidence, verifies CI on the exact final head,
   and reports that head. The PR remains unmerged.
3. Claude reviews that fixed PR head from a separate detached worktree. Claude
   does not read an implementation checkout while Codex is changing it.
4. Codex remains idle during the audit: no commits, merge or next-phase work.
5. A gate failure starts a bounded correction pass. Codex tests and commits the
   corrections, updates evidence, and hands over a new fixed head for re-audit.
6. A gate pass permits the next explicitly authorized step. It never authorizes
   live orders, credentials, paid calls or changes to the v1 runtime.

Phase 0 operator actions remain Dan-only. The source fixes do not restart the
old launcher, repair installed config, remove publisher state, provision a key,
retire the ladder or rotate Telegram credentials. None was executed in Phase 1.

The GitHub PR head and CI results are the audit target; a chat summary is not
substitute evidence. Phase 1 evidence is in [phase-1.md](roadmap-evidence/phase-1.md).
