import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import common


class StripeModeTest(unittest.TestCase):
    def mode_for(self, key):
        with patch.dict(os.environ, {"STRIPE_API_KEY": key}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()):
                return common.init_stripe()

    def test_secret_live_key_is_live(self):
        self.assertEqual(self.mode_for("sk_live_example"), "LIVE")

    def test_restricted_live_key_is_live(self):
        self.assertEqual(self.mode_for("rk_live_example"), "LIVE")

    def test_test_key_is_test(self):
        self.assertEqual(self.mode_for("sk_test_example"), "test")


if __name__ == "__main__":
    unittest.main()
