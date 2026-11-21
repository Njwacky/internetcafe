"""Do not let release builds silently omit the saved-work regression suite."""
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch


class ReleaseDiscoveryTests(unittest.TestCase):
    def test_release_runner_includes_existing_and_saved_work_tests(self):
        root = Path(__file__).resolve().parents[1]
        namespace = runpy.run_path(str(root / '.github' / 'run_tests.py'))
        result = Mock(failures=[], errors=[])
        with patch('unittest.TextTestRunner') as runner:
            runner.return_value.run.return_value = result
            self.assertEqual(namespace['main'](), 0)
        suite = runner.return_value.run.call_args.args[0]
        def ids(node):
            if isinstance(node, unittest.TestSuite):
                for child in node: yield from ids(child)
            else: yield node.id()
        names = list(ids(suite))
        self.assertTrue(any(n.startswith('test_services.') for n in names))
        self.assertTrue(any(n.startswith('test_saved_services.') for n in names))
        self.assertTrue(any(n.startswith('test_sales_outbox.') for n in names))
        self.assertFalse(any('_FailedTest' in n for n in names))


if __name__ == '__main__':
    unittest.main()
