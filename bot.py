"""Run with --setup to find your user ID, --check-db to check SQL, or no flag to serve."""

import argparse
import asyncio
import logging
import re

from telegram import Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import create_deep_linked_url

from config import Settings
from database import Repository

LOGGER = logging.getLogger(__name__)
CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{24}")
NOT_FOUND = "این لینک نامعتبر یا غیرفعال است."


class Handlers:
    def __init__(self, settings, repository=None, *, setup=False):
        self.settings = settings
        self.repository = repository
        self.setup = setup

    @staticmethod
    def private_message(update):
        # Only new messages, never edited messages or channel posts.
        return bool(update.message and update.effective_chat.type == "private")

    def is_admin(self, update):
        return bool(
            self.private_message(update)
            and update.effective_user
            and update.effective_user.id in self.settings.admin_ids
        )

    async def identify(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.private_message(update):
            await update.message.reply_text(f"شناسهٔ عددی حساب شما:\n{update.effective_user.id}")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.private_message(update):
            return
        if self.setup:
            await update.message.reply_text(
                "ربات در حالت راه‌اندازی است.\n"
                "برای دیدن شناسهٔ حساب خود، دستور /id را بفرستید.\n"
                "در این حالت آپلود و دریافت فایل فعال نیست."
            )
        elif self.is_admin(update):
            await update.message.reply_text(
                "عکس یا ویدئو بفرستید تا لینک مخصوص آن ساخته شود.\n"
                "عکس و ویدئو به صورت فایل پیوست نیز پذیرفته می‌شوند.\n"
                "برای هر عضو آلبوم یک لینک جداگانه ساخته می‌شود.\n"
                "غیرفعال کردن لینک: /revoke و سپس کد انتهای لینک\n"
                "شناسهٔ حساب: /id"
            )
        else:
            await update.message.reply_text("برای دریافت فایل، لینک مخصوص آن را باز کنید.")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.private_message(update):
            return
        if self.setup or not context.args:
            await self.help(update, context)
            return
        if len(context.args) != 1 or not CODE_PATTERN.fullmatch(context.args[0]):
            await update.message.reply_text(NOT_FOUND)
            return
        media = await asyncio.to_thread(self.repository.get, context.bot.id, context.args[0])
        if not media or not media.active:
            await update.message.reply_text(NOT_FOUND)
            return
        senders = {
            "photo": update.message.reply_photo,
            "video": update.message.reply_video,
            "document": update.message.reply_document,
        }
        # Plain text captions prevent uploaded text from becoming HTML or Markdown.
        await senders[media.kind](media.file_id, caption=media.caption)

    async def upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.private_message(update):
            return
        if self.setup:
            await self.help(update, context)
            return
        if not self.is_admin(update):
            await update.message.reply_text("ثبت فایل فقط برای مدیر ربات فعال است.")
            return
        message = update.message
        if message.photo:
            kind, media = "photo", message.photo[-1]
        elif message.video:
            kind, media = "video", message.video
        elif message.document and (message.document.mime_type or "").startswith(("image/", "video/")):
            # MIME is only a format hint; files are never opened or executed here.
            kind, media = "document", message.document
        else:
            await message.reply_text("لطفاً عکس یا ویدئو بفرستید.")
            return
        code = await asyncio.to_thread(
            self.repository.save,
            bot_id=context.bot.id,
            kind=kind,
            file_id=media.file_id,
            caption=message.caption,
            uploader_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            message_id=message.message_id,
        )
        link = create_deep_linked_url(context.bot.username, code)
        await message.reply_text(
            f"لینک این فایل:\n{link}\n\n"
            f"غیرفعال کردن لینک:\n/revoke {code}",
            do_quote=True,
        )

    async def revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.private_message(update):
            return
        if self.setup:
            await self.help(update, context)
            return
        if not self.is_admin(update):
            await update.message.reply_text("این دستور فقط برای مدیر ربات فعال است.")
            return
        if len(context.args) != 1 or not CODE_PATTERN.fullmatch(context.args[0]):
            await update.message.reply_text("بعد از /revoke، کد انتهای لینک را وارد کنید.")
            return
        changed = await asyncio.to_thread(self.repository.revoke, context.bot.id, context.args[0])
        await update.message.reply_text("لینک غیرفعال شد." if changed else NOT_FOUND)


async def on_error(update, context):
    # Exception messages or HTTP URLs can contain bot tokens; log the class only.
    LOGGER.error("Request failed: %s", type(context.error).__name__)
    if isinstance(context.error, Forbidden):
        return
    if not isinstance(update, Update) or not update.message:
        return
    if isinstance(context.error, RetryAfter):
        text = "درخواست‌ها زیاد شده است؛ کمی صبر کنید و دوباره تلاش کنید."
    elif isinstance(context.error, BadRequest):
        text = "تلگرام نتوانست این درخواست را انجام دهد. لطفاً با مدیر ربات تماس بگیرید."
    else:
        text = "در انجام درخواست مشکلی پیش آمد. کمی بعد دوباره تلاش کنید."
    try:
        await update.message.reply_text(text)
    except TelegramError:
        pass


def build_application(settings, repository=None, *, setup=False):
    handlers = Handlers(settings, repository, setup=setup)
    app = Application.builder().token(settings.token).concurrent_updates(False).build()
    private = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE
    app.add_handler(CommandHandler("id", handlers.identify, filters=private))
    app.add_handler(CommandHandler("start", handlers.start, filters=private))
    app.add_handler(CommandHandler("help", handlers.help, filters=private))
    app.add_handler(CommandHandler("revoke", handlers.revoke, filters=private))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, handlers.upload))
    app.add_error_handler(on_error)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setup", action="store_true", help="Only show user IDs; no database needed")
    mode.add_argument("--check-db", action="store_true", help="Connect and create the media table")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    # HTTP request logs may include Telegram bot tokens.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        settings = Settings.load(
            require_token=not args.check_db,
            require_admin=not (args.setup or args.check_db),
        )
        repository = None
        if not args.setup:
            repository = Repository(settings)
            repository.initialize()
        if args.check_db:
            print("Database connection OK. dbo.MediaFiles is ready.")
            return
        app = build_application(settings, repository, setup=args.setup)
        print("Starting bot in SETUP mode." if args.setup else "Starting media bot.")
        print("Keep this terminal open. Press Ctrl+C to stop.")
        # Keep queued updates on restart. Database source-message uniqueness avoids duplicate links.
        app.run_polling(allowed_updates=["message"], drop_pending_updates=False)
    except ValueError as error:
        parser.exit(1, f"Configuration error: {error}\n")
    except Exception as error:
        # Do not print connection strings, Telegram URLs, or raw exception text.
        parser.exit(1, f"Startup failed ({type(error).__name__}). See troubleshooting in README.md.\n")


if __name__ == "__main__":
    main()
