---
name: write-tests
description: Write the smallest useful pytest suite for code just implemented in the Spot the Spin repo (backend/app, backend/eval, scripts). Use automatically after implementing or changing any Python module in this project, and whenever the user asks for tests here. Covers the acceptance criteria, one failure case per public function, and the project's honesty invariants (no test-set leakage, exact evidence spans, no secrets in output, no live model calls). Deliberately small: no exhaustive matrices.
---

# write-tests

Tests here prove the acceptance criteria and the honesty rules. They do not chase coverage numbers. A small set that catches the real mistakes beats a large set that repeats the implementation.

## When to use
- Right after implementing a module, before reporting it done.
- After changing behaviour covered by an existing test.
- When the user asks for tests in this project.

## Steps

1. **Find the acceptance criteria.** Read the brief or the "Acceptance" section of your agent definition. Those criteria define what must be tested. If they are missing, ask yourself the question the user would ask and write that test.

2. **Place the file.** Mirror the source tree: `backend/app/core/aggregate.py` gets `backend/tests/core/test_aggregate.py`. Use `backend/tests/fixtures/<area>/` for small fixture files. Never create fixtures bigger than about 20 items or 50 KB.

3. **Write the minimal set.** Per module, typically 3 to 10 tests:
   - one happy path per public function;
   - one failure or edge case per public function (empty input, invalid input, timeout, the boundary value);
   - invariant tests from the list below when the module touches that rule.
   Do not parametrize into dozens of cases unless the rule is a table (for example the six technique labels).

4. **Keep tests honest and fast.**
   - No network. Mock HTTP at the transport layer (httpx `MockTransport` or equivalent).
   - No live AI Router. `CORE_MODE=stub` is set by `backend/tests/conftest.py`; keep it that way.
   - Never read `data/raw/` or `data/eval/` in tests.
   - Deterministic: fixed seeds, no wall-clock assertions.
   - Use `tmp_path` or the `tmp_db_path` fixture for files and databases.

5. **Run.** From the repo root:
   ```
   uv run pytest backend/tests/<area> -q
   ```
   Fix failures in the code or in the test, whichever is wrong. Never delete or skip a failing test to get green. If you believe a test is wrong, say so in your report.

6. **Report.** Give the number of tests, the command, the result, and any test you consider fragile.

## Invariant tests (use the ones that apply)
- **Eval not in training material.** Build the prompt and assert that no normalized eval text occurs in it. Use the overlap helper from `backend/eval`, or write a small one that reads `data/eval` only inside this test with a clear skip if the folder is absent.
- **Exact spans.** Every returned `evidence_spans` item is a substring of the input, and every `[start, end)` offset matches the text.
- **Disputed when unclear.** No majority with at least two votes gives `disputed=True` and `technique="no_technique"`.
- **Scoring anchors.** The worked example gives 87, 27, 100, and the round total 71.
- **No secrets in output.** `repr(Settings)` and any error message do not contain the API key value set in the test.
- **Stub isolation.** Stub results are written only under `backend/eval/results/stub/`.
- **Frozen check.** A modified file in a frozen eval version makes the runner refuse to run.

## Anti-patterns
- Snapshot tests of model output. Model output is not a contract.
- Tests that call `time.sleep` to wait for something. Use mocks or fixed clocks.
- One giant test with twenty assertions. Split by behaviour.
- Tests that depend on execution order or on files left by another test.
- Coverage-driven padding: a test that only asserts a getter returns what the constructor received.

## Output checklist
- [ ] Acceptance criteria each map to at least one test
- [ ] One failure case per public function
- [ ] Applicable invariant tests present
- [ ] No network, no live model, no `data/eval` read
- [ ] `uv run pytest ... -q` green, command and count reported
