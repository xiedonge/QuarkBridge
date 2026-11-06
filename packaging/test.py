import sys,unittest
from pathlib import Path
platform='macos' if sys.platform=='darwin' else 'windows'
sys.path.insert(0,str(Path(platform).resolve()))
def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item
suite=unittest.defaultTestLoader.discover(platform,pattern='test_*.py')
if platform=='macos':suite=unittest.TestSuite(t for t in flatten(suite) if 'gui' not in t.id().lower())
result=unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
