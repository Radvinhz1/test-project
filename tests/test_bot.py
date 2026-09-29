"""Offline behavior tests: no token, SQL Server, or Telegram requests required."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram import Chat, Message, PhotoSize, Update, User
from datetime import datetime, timezone

from bot import Handlers, NOT_FOUND, build_application
from config import Settings
from database import Media


CODE = "AbCdEfGhIjKlMnOpQrStUvWx"
SETTINGS = Settings(
    token="123456:TEST_ONLY_NOT_A_REAL_TOKEN",
    admin_ids=frozenset({42}),
    server=r"(localdb)\MSSQLLocalDB",
    database="TelegramUploader",
    driver="ODBC Driver 18 for SQL Server",
    trust_certificate="yes",
)


def make_update(user_id=42, chat_type="private", **kwargs):
    message = SimpleNamespace(
        photo=[], video=None, document=None, caption=None, message_id=10,
        reply_text=AsyncMock(), reply_photo=AsyncMock(), reply_video=AsyncMock(),
        reply_document=AsyncMock(),
    )
    for name, value in kwargs.items():
        setattr(message, name, value)
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=user_id, type=chat_type),
    )


def context(*args):
    return SimpleNamespace(args=list(args), bot=SimpleNamespace(id=123456, username="verySimpleUploaderbot"))


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repository = Mock()
        self.handlers = Handlers(SETTINGS, self.repository)

    async def test_non_admin_cannot_upload(self):
        update = make_update(user_id=99, photo=[SimpleNamespace(file_id="photo")])
        await self.handlers.upload(update, context())
        self.repository.save.assert_not_called()
        update.message.reply_text.assert_awaited_once()

    async def test_empty_admin_set_never_grants_access(self):
        handlers = Handlers(replace(SETTINGS, admin_ids=frozenset()), self.repository)
        await handlers.upload(make_update(photo=[SimpleNamespace(file_id="photo")]), context())
        self.repository.save.assert_not_called()

    async def test_setup_mode_cannot_save_even_for_admin(self):
        handlers = Handlers(SETTINGS, self.repository, setup=True)
        update = make_update(photo=[SimpleNamespace(file_id="photo")])
        await handlers.upload(update, context())
        await handlers.start(update, context(CODE))
        await handlers.revoke(update, context(CODE))
        self.repository.save.assert_not_called()
        self.repository.get.assert_not_called()
        self.repository.revoke.assert_not_called()

    async def test_group_messages_are_ignored(self):
        update = make_update(chat_type="group", photo=[SimpleNamespace(file_id="photo")])
        await self.handlers.upload(update, context())
        await self.handlers.start(update, context(CODE))
        self.repository.save.assert_not_called()
        self.repository.get.assert_not_called()
        update.message.reply_text.assert_not_awaited()

    async def test_upload_saves_largest_photo_and_returns_link(self):
        self.repository.save.return_value = CODE
        update = make_update(
            photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
            caption="عکس <b>آزمایشی</b>",
        )
        await self.handlers.upload(update, context())
        saved = self.repository.save.call_args.kwargs
        self.assertEqual(saved["file_id"], "large")
        self.assertEqual(saved["kind"], "photo")
        self.assertEqual(saved["caption"], "عکس <b>آزمایشی</b>")
        self.assertEqual(saved["uploader_id"], 42)
        self.assertIn(f"https://t.me/verySimpleUploaderbot?start={CODE}", update.message.reply_text.call_args.args[0])

    async def test_video_and_media_documents_are_saved(self):
        self.repository.save.return_value = CODE
        for attribute, media, kind in [
            ("video", SimpleNamespace(file_id="v"), "video"),
            ("document", SimpleNamespace(file_id="d", mime_type="video/mp4"), "document"),
            ("document", SimpleNamespace(file_id="i", mime_type="image/png"), "document"),
        ]:
            with self.subTest(attribute=attribute, kind=kind):
                update = make_update(**{attribute: media})
                await self.handlers.upload(update, context())
                self.assertEqual(self.repository.save.call_args.kwargs["kind"], kind)

    async def test_unrelated_documents_are_rejected(self):
        for mime in (None, "application/pdf", "application/octet-stream"):
            update = make_update(document=SimpleNamespace(file_id="x", mime_type=mime))
            await self.handlers.upload(update, context())
        self.repository.save.assert_not_called()

    async def test_links_retrieve_matching_type_without_parsing_caption(self):
        for kind in ("photo", "video", "document"):
            with self.subTest(kind=kind):
                self.repository.get.return_value = Media(CODE, kind, "stored-file", "<b>سلام</b>")
                update = make_update(user_id=99)
                await self.handlers.start(update, context(CODE))
                self.repository.get.assert_called_with(123456, CODE)
                getattr(update.message, f"reply_{kind}").assert_awaited_once_with(
                    "stored-file", caption="<b>سلام</b>"
                )

    async def test_unknown_and_revoked_links_do_not_send_media(self):
        for media in (None, Media(CODE, "photo", "stored", None, False)):
            self.repository.get.return_value = media
            update = make_update(user_id=99)
            await self.handlers.start(update, context(CODE))
            update.message.reply_text.assert_awaited_once_with(NOT_FOUND)
            update.message.reply_photo.assert_not_awaited()

    async def test_malformed_payload_never_reaches_database(self):
        for args in [("bad",), ("x" * 65,), ("' OR 1=1 --",), (CODE, CODE)]:
            await self.handlers.start(make_update(), context(*args))
        self.repository.get.assert_not_called()

    async def test_non_admin_cannot_revoke(self):
        await self.handlers.revoke(make_update(user_id=99), context(CODE))
        self.repository.revoke.assert_not_called()

    async def test_admin_can_revoke_link(self):
        self.repository.revoke.return_value = True
        update = make_update()
        await self.handlers.revoke(update, context(CODE))
        self.repository.revoke.assert_called_once_with(123456, CODE)
        update.message.reply_text.assert_awaited_once_with("لینک غیرفعال شد.")


class WiringTests(unittest.TestCase):
    def test_real_telegram_filters_accept_new_private_upload_not_edits_or_groups(self):
        app = build_application(SETTINGS, Mock())  # Does not initialize or contact Telegram.
        handler = app.handlers[0][-1]
        def message(chat_type="private"):
            return Message(
                message_id=1, date=datetime.now(timezone.utc),
                chat=Chat(42, chat_type), from_user=User(42, "Admin", False),
                photo=[PhotoSize("f", "unique", 10, 10)],
            )
        self.assertTrue(handler.check_update(Update(1, message=message())))
        self.assertFalse(handler.check_update(Update(2, edited_message=message())))
        self.assertFalse(handler.check_update(Update(3, message=message("group"))))

    def test_normal_startup_requires_admin_but_setup_does_not(self):
        with patch("config.load_dotenv"), patch.dict("os.environ", {"BOT_TOKEN": SETTINGS.token}, clear=True):
            with self.assertRaisesRegex(ValueError, "ADMIN_IDS"):
                Settings.load()
            settings = Settings.load(require_admin=False)
            self.assertEqual(settings.admin_ids, frozenset())


if __name__ == "__main__":
    unittest.main()
