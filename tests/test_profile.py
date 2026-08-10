import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import paths as paths_module
from bot import profile as profile_module
from bot.profile import load_profile
from bot.validate_profile_env import validate_profile_env


class ProfileTests(unittest.TestCase):
    def tearDown(self):
        paths_module.refresh_runtime_dirs()

    def test_paper_profile_sets_small_account_defaults(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            os.environ.pop("RESEARCH_STARTING_EQUITY", None)
            load_profile("paper")
            self.assertEqual(os.environ["ALPACA_PAPER"], "true")
            self.assertEqual(os.environ["SYMBOL"], "TSLA")
            self.assertEqual(os.environ["RESEARCH_STARTING_EQUITY"], "150")
            self.assertIn("runtime", os.environ["BOT_DATA_DIR"])
            self.assertIn("paper", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_live_profile_disables_paper_mode(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            load_profile("live")
            self.assertEqual(os.environ["ALPACA_PAPER"], "false")
            self.assertEqual(os.environ["SYMBOL"], "TSLA")
            self.assertIn("live", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_paper_options_profile_enables_options_defaults(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            load_profile("paper", "options")
            self.assertEqual(os.environ["ALPACA_PAPER"], "true")
            self.assertEqual(os.environ["SYMBOL"], "NVDA")
            self.assertEqual(os.environ["OPTION_SYMBOLS"], "NVDA,TSLA")
            self.assertEqual(os.environ["IS_OPTIONS"], "true")
            self.assertEqual(os.environ["IS_CRYPTO"], "false")
            self.assertEqual(os.environ["ALLOW_SHORTS"], "true")
            self.assertEqual(os.environ["ALLOW_OVERNIGHT_HOLDING"], "true")
            self.assertEqual(os.environ["FLATTEN_BEFORE_CLOSE_MINUTES"], "0")
            self.assertIn("paper_options", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_live_options_profile_disables_paper_mode(self):
        original = dict(os.environ)
        try:
            os.environ.pop("BOT_DATA_DIR", None)
            os.environ.pop("BOT_LOGS_DIR", None)
            os.environ.pop("BOT_REPORTS_DIR", None)
            load_profile("live", "options")
            self.assertEqual(os.environ["ALPACA_PAPER"], "false")
            self.assertEqual(os.environ["SYMBOL"], "NVDA")
            self.assertEqual(os.environ["OPTION_SYMBOLS"], "NVDA,TSLA")
            self.assertEqual(os.environ["IS_OPTIONS"], "true")
            self.assertIn("live_options", os.environ["BOT_DATA_DIR"])
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_unsupported_market_raises(self):
        original = dict(os.environ)
        try:
            with self.assertRaises(ValueError):
                load_profile("paper", "btc")
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_profile_env_overrides_base_env(self):
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / ".env").write_text("RESEARCH_STARTING_EQUITY=100000\nSYMBOL=QQQ\n", encoding="utf-8")
            (root / "config" / "paper_spy.env").write_text(
                "RESEARCH_STARTING_EQUITY=250\nSYMBOL=SPY\n",
                encoding="utf-8",
            )
            try:
                os.environ.pop("BOT_DATA_DIR", None)
                os.environ.pop("BOT_LOGS_DIR", None)
                os.environ.pop("BOT_REPORTS_DIR", None)
                os.environ.pop("RESEARCH_STARTING_EQUITY", None)
                os.environ.pop("SYMBOL", None)
                with patch.object(profile_module, "APP_ROOT", root):
                    load_profile("paper")
                self.assertEqual(os.environ["RESEARCH_STARTING_EQUITY"], "250")
                self.assertEqual(os.environ["SYMBOL"], "SPY")
            finally:
                os.environ.clear()
                os.environ.update(original)

    def test_profile_env_symbol_and_session_flags_win_over_market_defaults(self):
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / ".env").write_text("SYMBOL=SPY\n", encoding="utf-8")
            (root / "config" / "live_spy.env").write_text(
                "SYMBOL=QQQ\n"
                "IS_CRYPTO=false\n"
                "ALLOW_OVERNIGHT_HOLDING=true\n"
                "FLATTEN_BEFORE_CLOSE_MINUTES=0\n",
                encoding="utf-8",
            )
            try:
                for key in ("SYMBOL", "IS_CRYPTO", "ALLOW_OVERNIGHT_HOLDING", "FLATTEN_BEFORE_CLOSE_MINUTES"):
                    os.environ.pop(key, None)
                with patch.object(profile_module, "APP_ROOT", root):
                    load_profile("live", "spy")
                self.assertEqual(os.environ["SYMBOL"], "QQQ")
                self.assertEqual(os.environ["ALLOW_OVERNIGHT_HOLDING"], "true")
                self.assertEqual(os.environ["FLATTEN_BEFORE_CLOSE_MINUTES"], "0")
            finally:
                os.environ.clear()
                os.environ.update(original)

    def test_market_defaults_still_fill_gaps_when_profile_env_omits_them(self):
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / "config" / "live_spy.env").write_text("MAX_DAILY_LOSS=5\n", encoding="utf-8")
            try:
                for key in ("SYMBOL", "IS_CRYPTO", "ALLOW_OVERNIGHT_HOLDING", "FLATTEN_BEFORE_CLOSE_MINUTES"):
                    os.environ.pop(key, None)
                with patch.object(profile_module, "APP_ROOT", root):
                    load_profile("live", "spy")
                self.assertEqual(os.environ["SYMBOL"], "SPY")
                self.assertEqual(os.environ["ALLOW_OVERNIGHT_HOLDING"], "false")
                self.assertEqual(os.environ["FLATTEN_BEFORE_CLOSE_MINUTES"], "5")
            finally:
                os.environ.clear()
                os.environ.update(original)

    def test_spy_profile_env_contract_matches_config_file(self):
        original = dict(os.environ)
        try:
            resolved = validate_profile_env("live", "spy")
            self.assertEqual(resolved["SYMBOL"], "TSLA")
        finally:
            os.environ.clear()
            os.environ.update(original)


if __name__ == "__main__":
    unittest.main()
