"""Run the whole gpu_setperm suite:  $PY gpu_setperm_test.py   (SP_NO_GPU=1 skips GPU tests)"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(HERE, pattern="sp_test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
