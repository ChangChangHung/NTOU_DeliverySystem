"""No-dependency checks for the local acceptance database safety boundary."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mysql_support import LocalSettings  # noqa: E402


class LocalDatabaseSafetyTests(unittest.TestCase):
    def environment(self, **changes):
        return {
            "NTOU_ACCEPTANCE_ALLOW_CREATE": "1",
            "DB_USER": "synthetic-local-user",
            "DB_PASSWORD": "synthetic-password-not-a-real-secret",
            **changes,
        }

    def test_requires_explicit_schema_creation_opt_in(self):
        with patch.dict("os.environ", self.environment(NTOU_ACCEPTANCE_ALLOW_CREATE="0"), clear=True):
            with self.assertRaisesRegex(RuntimeError, "ALLOW_CREATE"):
                LocalSettings.from_environment("test")

    def test_rejects_remote_or_wildcard_database_hosts(self):
        for host in ["example.com", "192.168.1.10", "0.0.0.0", "localhost.example.com"]:
            with self.subTest(host=host), patch.dict("os.environ", self.environment(DB_HOST=host), clear=True):
                with self.assertRaisesRegex(RuntimeError, "only allow"):
                    LocalSettings.from_environment("test")

    def test_rejects_shared_schema_names_and_sql_identifier_injection(self):
        for name in ["food_order_db", "mysql", "ntou_acceptance_x`", "ntou_acceptance_", "ntou_acceptance_" + "x" * 49]:
            with self.subTest(database=name), patch.dict("os.environ", self.environment(DB_NAME=name), clear=True):
                with self.assertRaisesRegex(RuntimeError, "DB_NAME"):
                    LocalSettings.from_environment("test")

    def test_requires_explicit_credentials_and_valid_port(self):
        for changes in [{"DB_USER": ""}, {"DB_PORT": "abc"}, {"DB_PORT": "0"}, {"DB_PORT": "65536"}]:
            with self.subTest(changes=changes), patch.dict("os.environ", self.environment(**changes), clear=True):
                with self.assertRaises(RuntimeError):
                    LocalSettings.from_environment("test")
        missing_password = self.environment()
        missing_password.pop("DB_PASSWORD")
        with patch.dict("os.environ", missing_password, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DB_USER and DB_PASSWORD"):
                LocalSettings.from_environment("test")

    def test_defaults_to_local_3307_and_unique_new_schema_without_exposing_password(self):
        with patch.dict("os.environ", self.environment(), clear=True):
            first = LocalSettings.from_environment("test")
            second = LocalSettings.from_environment("test")
        self.assertEqual(first.host, "127.0.0.1")
        self.assertEqual(first.port, 3307)
        self.assertTrue(first.database.startswith("ntou_acceptance_test_"))
        self.assertNotEqual(first.database, second.database)
        self.assertNotIn(first.password, repr(first))


if __name__ == "__main__":
    unittest.main(verbosity=2)
