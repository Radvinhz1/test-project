"""Regression tests for LocalDB and remote-server encryption settings."""
import unittest
from unittest.mock import patch
from config import Settings

class EncryptionTests(unittest.TestCase):
    def settings(self, values):
        with patch("config.load_dotenv"), patch.dict("os.environ", values, clear=True):
            return Settings.load(require_token=False, require_admin=False)

    def test_default_localdb_does_not_require_encryption(self):
        settings = self.settings({})
        self.assertEqual(settings.encrypt, "no")
        self.assertIn("Encrypt=no;", settings.connection_string())

    def test_driver17_localdb_uses_local_default(self):
        settings = self.settings({"SQL_DRIVER": "ODBC Driver 17 for SQL Server",
                                  "SQL_SERVER": r"(LocalDB)\MSSQLLocalDB"})
        self.assertEqual(settings.encrypt, "no")
        self.assertIn("ODBC Driver 17 for SQL Server", settings.connection_string())

    def test_remote_server_keeps_encryption(self):
        settings = self.settings({"SQL_SERVER": "sql.example.com"})
        self.assertEqual(settings.encrypt, "yes")
        self.assertIn("Encrypt=yes;", settings.connection_string())

    def test_explicit_encryption_option_takes_precedence(self):
        settings = self.settings({"SQL_ENCRYPT": "YES"})
        self.assertEqual(settings.encrypt, "yes")

    def test_invalid_encryption_setting_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SQL_ENCRYPT"):
            self.settings({"SQL_ENCRYPT": "maybe"})

if __name__ == "__main__":
    unittest.main()
