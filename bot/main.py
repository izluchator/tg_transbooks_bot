import logging
from telegram.ext import ApplicationBuilder
from bot.config import TELEGRAM_BOT_TOKEN
from bot.handlers import register_handlers
from bot.database import close_db

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("weasyprint").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env")
        raise SystemExit(1)

    logger.info("Starting PDF Translator Bot...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
    register_handlers(app)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
