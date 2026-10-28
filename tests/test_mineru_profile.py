import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "mineru_profile", Path(__file__).resolve().parents[1] / "scripts/set-mineru-profile.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MineruProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "secrets").mkdir()
        self.env = self.root / "secrets/compiler.env"
        self.original = "# retain comment\nGPT_API_KEY='quoted # value' # tail\nOTHER=untouched\n"
        self.env.write_text(self.original)
        self.credentials = self.root / "credentials.json"
        self.credentials.write_text(json.dumps({
            "api_url": module.AUTODL_URL, "api_token": "test-token'$",
        }))

    def apply(self, mode):
        return module.profile(mode, self.root, self.credentials)

    def test_temporary_switch_preserves_other_lines_and_lab_removes_overrides(self):
        with patch.object(module, "health", return_value={
            "status": "healthy", "protocol_version": 1,
            "workers_ready": 1, "workers_total": 4,
        }) as health:
            result = self.apply("autodl")
        health.assert_called_once_with("test-token'$")
        self.assertEqual(result["workers_ready"], 1)
        self.assertTrue(self.env.read_text().startswith(self.original))
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        self.assertEqual(self.apply("status"), {
            "url": module.AUTODL_URL, "concurrency": "4", "token_present": True,
        })
        self.assertNotIn("test-token", json.dumps(self.apply("status")))
        self.apply("lab")
        self.assertEqual(self.env.read_text(), self.original)
        self.assertEqual(self.apply("status"), {
            "url": module.LAB_URL, "concurrency": "1", "token_present": False,
        })

    def test_jobs_api_profile_needs_health_and_drops_the_token(self):
        with patch.object(module, "health", return_value={
            "status": "healthy", "protocol_version": 1, "workers_ready": 4,
        }):
            self.apply("autodl")
        with patch.object(module, "parse_health", return_value={"status": "down"}), \
                self.assertRaises(ValueError):
            self.apply("parse")
        self.assertIn(module.AUTODL_URL, self.env.read_text())
        with patch.object(module, "parse_health", return_value={"status": "ok"}):
            self.apply("parse")
        self.assertTrue(self.env.read_text().startswith(self.original))
        self.assertIn("COMPILER_MINERU_TIMEOUT_SECONDS=1800", self.env.read_text())
        self.assertEqual(self.apply("status"), {
            "url": module.PARSE_URL, "concurrency": "4", "token_present": False,
        })
        self.apply("lab")
        self.assertEqual(self.env.read_text(), self.original)

    def test_wrong_url_does_not_write_or_request_health(self):
        self.credentials.write_text(json.dumps({"api_url": "http://example.com", "api_token": "x"}))
        with patch.object(module, "health") as health, self.assertRaises(ValueError):
            self.apply("autodl")
        health.assert_not_called()
        self.assertEqual(self.env.read_text(), self.original)

    def test_unready_or_wrong_protocol_does_not_write(self):
        for protocol, ready in [(2, 4), (1, 0), (True, 4)]:
            with self.subTest(protocol=protocol, ready=ready), patch.object(
                module, "health", return_value={
                    "status": "healthy", "protocol_version": protocol,
                    "workers_ready": ready,
                },
            ), self.assertRaises(ValueError):
                self.apply("autodl")
            self.assertEqual(self.env.read_text(), self.original)


if __name__ == "__main__":
    unittest.main()
