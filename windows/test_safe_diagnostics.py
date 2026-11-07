import sys,unittest
from pathlib import Path
from safe_diagnostics import failure_detail

class DiagnosticsTests(unittest.TestCase):
    def test_no_exception_values_or_paths_in_report(self):
        secret='private-cookie-value'
        try:raise LookupError(secret)
        except LookupError as error:report=failure_detail(error)
        self.assertIn('LookupError',report)
        self.assertNotIn(secret,report)
        self.assertNotIn(str(Path.home()),report)
    def test_offline_api_error_recovery_contract(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'packaging'))
        from frozen_contract import check_error_recovery
        check_error_recovery()
