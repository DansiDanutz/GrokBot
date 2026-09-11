# Grid KuCoin verification records

Both gates ran before every commit. From Task 1 onward, verification used a
separate detached temporary worktree containing the exact staged tree, checked
with `git write-tree`, so concurrent uncommitted tasks could not contaminate
results. No acceptance checkout was used. The final handoff also runs gates
in the implementation checkout. Gate tails below are captured command output.

## prereg — 2945fcd

`npm run verify`

```text
✔ source lock distinguishes development from the immutable v1 import (0.704042ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1007.967167

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.528s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (120 tracked files scanned).
```

## semantics — c6e5ef8

`npm run verify`

```text
✔ source lock distinguishes development from the immutable v1 import (0.757958ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 921.697167

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.539s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (122 tracked files scanned).
```

## task1 — e2f735d

`npm run verify`

```text
ℹ duration_ms 915.103917

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.578s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 48 tests in 0.042s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (127 tracked files scanned).
```

## task2 — 62e179b

`npm run verify`

```text
ℹ duration_ms 819.034917

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.522s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 69 tests in 1.293s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (131 tracked files scanned).
```

## task3 — 09e66b6

`npm run verify`

```text
ℹ duration_ms 908.261

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.512s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 104 tests in 1.406s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (137 tracked files scanned).
```

## task4 — 69c6702

`npm run verify`

```text
ℹ duration_ms 1044.844834

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.045s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 139 tests in 1.905s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (143 tracked files scanned).
```

## task6 — d939516

`npm run verify`

```text
ℹ duration_ms 938.460709

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.588s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 158 tests in 2.076s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (148 tracked files scanned).
```

## Five-percent amendment — f5aabd3

`npm run verify`

```text
ℹ duration_ms 846.687

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.619s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 171 tests in 2.439s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (149 tracked files scanned).
```

## Streaming correction — 50e963f

`npm run verify`

```text
ℹ duration_ms 1002.058708

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.520s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 172 tests in 2.427s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (149 tracked files scanned).
```

## Final evidence handoff — documentation commit

Captured in the implementation checkout before the evidence commit.
After adding these tails, both gates were rerun on the final staged files.

`npm run verify`

```text
ℹ duration_ms 872.756667

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.537s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 172 tests in 2.450s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (156 tracked files scanned).
```

## Final-gate retry disclosure

The first final rerun timed out in the existing isolated launcher integration
fixture after 5 seconds (37/38 Node tests). No evidence commit was made on
that failed gate. An immediate unchanged-source rerun passed all 489 tests
and the secret gate. The final staged documentation was verified again after
recording this disclosure. No launcher or runtime source was changed.

`npm run verify`

```text
ℹ duration_ms 864.7715

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.534s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 172 tests in 2.515s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (156 tracked files scanned).
```
