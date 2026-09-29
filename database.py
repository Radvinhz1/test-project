"""SQL Server storage. Media bytes stay on Telegram; only metadata is stored here."""

from contextlib import closing
from dataclasses import dataclass
import secrets

from config import Settings


@dataclass(frozen=True)
class Media:
    code: str
    kind: str
    file_id: str
    caption: str | None
    active: bool = True


SCHEMA = """
IF OBJECT_ID(N'dbo.MediaFiles', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.MediaFiles (
        bot_id BIGINT NOT NULL,
        code VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL,
        kind VARCHAR(10) NOT NULL CHECK (kind IN ('photo', 'video', 'document')),
        file_id NVARCHAR(1024) NOT NULL,
        caption NVARCHAR(MAX) NULL,
        uploader_id BIGINT NOT NULL,
        source_chat_id BIGINT NOT NULL,
        source_message_id BIGINT NOT NULL,
        active BIT NOT NULL DEFAULT 1,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_MediaFiles PRIMARY KEY (bot_id, code),
        CONSTRAINT UQ_MediaFiles_Source UNIQUE (bot_id, source_chat_id, source_message_id)
    );
END;
"""


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self):
        import pyodbc

        # Each worker owns its connection; connections are never shared across threads.
        connection = pyodbc.connect(self.settings.connection_string(), timeout=10)
        connection.timeout = 15
        return connection

    def initialize(self):
        import pyodbc

        if self.settings.driver not in pyodbc.drivers():
            raise ValueError(
                "SQL_DRIVER is not installed. Install Microsoft ODBC Driver 18 "
                "or set SQL_DRIVER to an installed driver. See README.md."
            )
        with closing(self.connect()) as connection:
            connection.execute(SCHEMA)
            connection.commit()

    def save(self, *, bot_id, kind, file_id, caption, uploader_id, chat_id, message_id):
        import pyodbc

        # Replay of the same Telegram message returns the existing code.
        # The database's unique constraint is the final duplicate guard.
        for _ in range(3):
            with closing(self.connect()) as connection:
                row = connection.execute(
                    "SELECT code FROM dbo.MediaFiles WHERE bot_id=? "
                    "AND source_chat_id=? AND source_message_id=?",
                    bot_id, chat_id, message_id,
                ).fetchone()
                if row:
                    return row[0]
                code = secrets.token_urlsafe(18)  # 144 bits, 24 URL-safe characters
                try:
                    connection.execute(
                        "INSERT INTO dbo.MediaFiles "
                        "(bot_id, code, kind, file_id, caption, uploader_id, "
                        "source_chat_id, source_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        bot_id, code, kind, file_id, caption, uploader_id, chat_id, message_id,
                    )
                    connection.commit()
                    return code
                except pyodbc.IntegrityError as error:
                    connection.rollback()
                    # Retry only SQL Server unique constraint violations.
                    if not any(number in str(error) for number in ("2601", "2627")):
                        raise
        raise RuntimeError("Could not allocate a unique media link; retry the upload.")

    def get(self, bot_id, code):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT code, kind, file_id, caption, active FROM dbo.MediaFiles "
                "WHERE bot_id=? AND code=?", bot_id, code,
            ).fetchone()
            return Media(*row) if row else None

    def revoke(self, bot_id, code):
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                "UPDATE dbo.MediaFiles SET active=0 "
                "OUTPUT INSERTED.code WHERE bot_id=? AND code=? AND active=1",
                bot_id, code,
            )
            changed = cursor.fetchone() is not None
            # Consume results before committing an OUTPUT statement.
            cursor.fetchall()
            connection.commit()
            return changed
