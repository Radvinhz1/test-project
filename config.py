"""Read local settings without putting credentials in source code."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    admin_ids: frozenset[int]
    server: str
    database: str
    driver: str
    trust_certificate: str
    encrypt: str = "yes"

    @classmethod
    def load(cls, *, require_token=True, require_admin=True):
        load_dotenv(ROOT / ".env")
        token = os.getenv("BOT_TOKEN", "").strip()
        if require_token and (not token or token.startswith("PUT_")):
            raise ValueError("Set BOT_TOKEN in .env using your BotFather token.")
        try:
            admins = frozenset(
                int(item.strip())
                for item in os.getenv("ADMIN_IDS", "").split(",")
                if item.strip()
            )
        except ValueError:
            raise ValueError("ADMIN_IDS must contain numeric user IDs separated by commas.") from None
        if any(item <= 0 for item in admins):
            raise ValueError("ADMIN_IDS must contain positive user IDs.")
        if require_admin and not admins:
            raise ValueError("Run python bot.py --setup, send /id, then set ADMIN_IDS in .env.")
        trust = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "no").strip().lower()
        if trust not in {"yes", "no"}:
            raise ValueError("SQL_TRUST_SERVER_CERTIFICATE must be yes or no.")
        server = os.getenv("SQL_SERVER", r"(localdb)\MSSQLLocalDB").strip()
        # LocalDB uses a local connection that may not support transport encryption.
        # Keep encryption enabled by default for other SQL Server connections.
        default_encrypt = "no" if server.lower().startswith("(localdb)\\") else "yes"
        encrypt = os.getenv("SQL_ENCRYPT", default_encrypt).strip().lower()
        if encrypt not in {"yes", "no"}:
            raise ValueError("SQL_ENCRYPT must be yes or no.")
        return cls(
            token=token,
            admin_ids=admins,
            server=server,
            database=os.getenv("SQL_DATABASE", "TelegramUploader"),
            driver=os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server"),
            trust_certificate=trust,
            encrypt=encrypt,
        )

    def connection_string(self):
        # ODBC brace escaping also handles semicolons in setting values.
        def quote(value):
            return "{" + value.replace("}", "}}") + "}"

        return (
            f"DRIVER={quote(self.driver)};SERVER={quote(self.server)};"
            f"DATABASE={quote(self.database)};Trusted_Connection=yes;"
            f"Encrypt={self.encrypt};TrustServerCertificate={self.trust_certificate};"
        )
