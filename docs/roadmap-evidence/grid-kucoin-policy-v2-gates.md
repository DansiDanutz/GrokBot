# Policy-v2 gate evidence

Every source task was staged separately and both gates passed on an identical
staged Git tree in a separate temporary worktree before committing. This
excludes concurrent uncommitted work and preserves PR #14's frozen checkout.
The final handoff also runs both gates in the implementation checkout.

## prereg — 200de6b

`npm run verify`

```text
ℹ duration_ms 1043.854458

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.550s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 172 tests in 2.436s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (158 tracked files scanned).
```

## adaptive — f654c35

`npm run verify`

```text
ℹ duration_ms 877.6775

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.562s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 182 tests in 2.691s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (158 tracked files scanned).
```

## sizing — d75e86e

`npm run verify`

```text
ℹ duration_ms 883.501959

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.555s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 184 tests in 2.692s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (158 tracked files scanned).
```

## preview — 5af1382

`npm run verify`

```text
ℹ duration_ms 1055.162625

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.119s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 185 tests in 2.816s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (158 tracked files scanned).
```

## portfolio — a65d59a

`npm run verify`

```text
ℹ duration_ms 930.989667

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.590s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 201 tests in 3.166s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (158 tracked files scanned).
```

## operator — 9b37de2

`npm run verify`

```text
ℹ duration_ms 984.216625

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.571s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 215 tests in 6.546s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (158 tracked files scanned).
```

## timer — 788431d

`npm run verify`

```text
ℹ duration_ms 906.907291

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.553s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 221 tests in 6.549s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (160 tracked files scanned).
```

## Final evidence commit

Both gates ran in the implementation checkout before recording these tails.
They were rerun on the final staged documentation before committing.

`npm run verify`

```text
ℹ duration_ms 1031.946417

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.565s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 221 tests in 6.534s

OK
```

`npm run verify:secrets`

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (164 tracked files scanned).
```
