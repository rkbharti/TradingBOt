import logging

import requests

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from config.settings import (
    ENABLE_TELEGRAM,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

VPS_BASE_URL = "http://localhost:8000"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("telegram-bot")

AUTHORIZED_CHAT_ID = str(TELEGRAM_CHAT_ID)


def is_authorized(update: Update) -> bool:
    if not update.effective_chat:
        return False

    incoming_chat_id = str(update.effective_chat.id)

    return incoming_chat_id == AUTHORIZED_CHAT_ID


async def unauthorized_reply(update: Update):
    await update.message.reply_text("❌ Unauthorized")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await unauthorized_reply(update)

    await update.message.reply_text(
        "🤖 TradingBot Telegram Control Online\n\n"
        "Commands:\n"
        "/status\n"
        "/pause\n"
        "/resume\n"
        "/summary\n"
        "/ping\n"
        "/help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start_command(update, context)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await unauthorized_reply(update)

    await update.message.reply_text("🏓 Pong")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await unauthorized_reply(update)

    try:
        response = requests.get(
            f"{VPS_BASE_URL}/bot/status",
            timeout=5,
        )

        data = response.json()

        status = "ACTIVE ✅" if data["trading"] else "PAUSED ⏸️"

        await update.message.reply_text(
            f"🤖 Bot Status\n\n"
            f"Trading: {status}\n"
            f"Updated At:\n{data['updated_at']}"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Status request failed\n{e}"
        )


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await unauthorized_reply(update)

    try:
        requests.post(
            f"{VPS_BASE_URL}/bot/pause",
            timeout=5,
        )

        await update.message.reply_text(
            "⏸️ Trading Bot Paused"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Pause failed\n{e}"
        )


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await unauthorized_reply(update)

    try:
        requests.post(
            f"{VPS_BASE_URL}/bot/resume",
            timeout=5,
        )

        await update.message.reply_text(
            "▶️ Trading Bot Resumed"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Resume failed\n{e}"
        )


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await unauthorized_reply(update)

    try:
        response = requests.post(
            f"{VPS_BASE_URL}/daily-summary/send",
            timeout=10,
        )

        if response.status_code == 200:
            await update.message.reply_text(
                "📊 Daily summary triggered"
            )
        else:
            await update.message.reply_text(
                f"❌ Summary trigger failed ({response.status_code})"
            )

    except Exception as e:
        await update.message.reply_text(
            f"❌ Summary request failed\n{e}"
        )


def main():
    if not ENABLE_TELEGRAM:
        logger.error("Telegram disabled in config")
        return

    if not TELEGRAM_BOT_TOKEN:
        logger.error("Missing TELEGRAM_BOT_TOKEN")
        return

    logger.info("Starting Telegram bot polling service...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("summary", summary_command))

    logger.info("Telegram bot online")

    app.run_polling()


if __name__ == "__main__":
    main()