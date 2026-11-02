"""
Runs Mitra's test suites and prints a summary.

    python tests/run.py                     every suite
    python tests/run.py smoke regression    chosen suites

Set PERF_BUDGET_SCALE to relax performance budgets on slower machines.
"""
import os
import sys
import time
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)

# Fastest first, so a broken build fails quickly
SUITES = ["smoke", "unit", "regression", "integration", "nonfunctional", "performance"]


def main():
    chosen = sys.argv[1:] or SUITES
    unknown = [s for s in chosen if s not in SUITES]
    if unknown:
        sys.exit(f"Unknown suite: {', '.join(unknown)}. Choose from: {', '.join(SUITES)}")
    rows, passed = [], True
    for name in chosen:
        print(f"\n=== {name} ===", flush=True)
        tests = unittest.TestLoader().discover(os.path.join(TESTS, name), top_level_dir=TESTS)
        started = time.time()
        result = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(tests)
        passed = passed and result.wasSuccessful() and result.testsRun > 0
        rows.append((name, result.testsRun, len(result.failures) + len(result.errors), len(result.expectedFailures),
                     len(result.skipped), time.time() - started))
    print(f"\n{'Suite':<15}{'Tests':>7}{'Failed':>8}{'Known gaps':>12}{'Skipped':>9}{'Seconds':>9}")
    for name, run, failed, gaps, skipped, seconds in rows:
        print(f"{name:<15}{run:>7}{failed:>8}{gaps:>12}{skipped:>9}{seconds:>9.1f}")
    print(f"{'Total':<15}{sum(r[1] for r in rows):>7}{sum(r[2] for r in rows):>8}{sum(r[3] for r in rows):>12}"
          f"{sum(r[4] for r in rows):>9}{sum(r[5] for r in rows):>9.1f}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
