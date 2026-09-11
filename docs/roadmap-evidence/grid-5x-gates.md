# Grid 5x verification transcripts

Every task was staged separately before its verification and secret gates.
Baseline ran before implementation. No gate calls a provider or opens market data.

## baseline

```sh
npm run verify > /tmp/grid5x-baseline-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.806375ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 985.673208

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.618s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-baseline-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Committed-secret gate passed (117 tracked files scanned).
```

## docs

```sh
npm run verify > /tmp/grid5x-docs-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.510209ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 832.234583

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.578s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-docs-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (118 tracked files scanned).
```

## prereg

```sh
npm run verify > /tmp/grid5x-prereg-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.529292ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 876.665833

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.531s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-prereg-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (119 tracked files scanned).
```

## core

```sh
npm run verify > /tmp/grid5x-core-verify.log 2>&1
```

```text
ℹ duration_ms 991.99375

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.551s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 22 tests in 0.003s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-core-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (122 tracked files scanned).
```

## sizing

```sh
npm run verify > /tmp/grid5x-sizing-verify.log 2>&1
```

```text
ℹ duration_ms 836.964125

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.564s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 26 tests in 0.003s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-sizing-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (124 tracked files scanned).
```

## scanner

```sh
npm run verify > /tmp/grid5x-scanner-verify.log 2>&1
```

```text
ℹ duration_ms 890.293

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.547s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 47 tests in 0.028s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-scanner-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (128 tracked files scanned).
```

## replacement

```sh
npm run verify > /tmp/grid5x-replacement-verify.log 2>&1
```

```text
ℹ duration_ms 813.718791

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.513s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 54 tests in 0.032s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-replacement-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (130 tracked files scanned).
```

## replay

```sh
npm run verify > /tmp/grid5x-replay-verify.log 2>&1
```

```text
ℹ duration_ms 880.539875

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.541s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 67 tests in 0.067s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-replay-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (134 tracked files scanned).
```

## operator

```sh
npm run verify > /tmp/grid5x-operator-verify.log 2>&1
```

```text
ℹ duration_ms 878.663916

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.587s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 72 tests in 0.077s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-operator-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (138 tracked files scanned).
```

## accounting

```sh
npm run verify > /tmp/grid5x-accounting-verify.log 2>&1
```

```text
ℹ duration_ms 1072.390792

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.094s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 78 tests in 0.085s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-accounting-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (138 tracked files scanned).
```

## replay-review

```sh
npm run verify > /tmp/grid5x-replay-review-verify.log 2>&1
```

```text
ℹ duration_ms 857.961208

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.616s

OK

> danslab-grokbot@0.1.0 test:grid
> python3 -m unittest discover -s trader/tests -p "test_*.py" -q

----------------------------------------------------------------------
Ran 82 tests in 0.089s

OK
```

```sh
npm run verify:secrets > /tmp/grid5x-replay-review-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (138 tracked files scanned).
```
