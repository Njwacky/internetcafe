"""Run the Café Desk unit-test suite and surface failures as GitHub annotations.

`unittest` failures otherwise only appear in the runner log. Emitting `::error::`
workflow commands makes the failing test ids visible through the check-run
annotations API, so build failures are diagnosable even without log access.
"""
import sys
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("test_services")
    stream = sys.stdout
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, buffer=True)
    result = runner.run(suite)
    failures = [str(t) for t, _ in list(result.failures) + list(result.errors)]
    for name in failures:
        # Multi-line GitHub workflow command: first line becomes the annotation.
        detail = ""
        for test, trace in list(result.failures) + list(result.errors):
            if str(test) == name:
                detail = trace.strip().splitlines()[-1][:400]
                break
        print(f"::error title=Test failed::{name} :: {detail}")
    if failures:
        print(f"::notice:: {len(failures)} failing test(s): {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
