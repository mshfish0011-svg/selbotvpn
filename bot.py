import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")


def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🛒 خرید سرویس", callback_data="buy"),
            InlineKeyboardButton("👤 حساب من", callback_data="account"),
        ],
        [
            InlineKeyboardButton("📦 سرویس‌های من", callback_data="services"),
            InlineKeyboardButton("🎫 پشتیبانی", callback_data="support"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 به NovaLinkVPN خوش اومدی\n\n"
        "🌐 اتصال سریع، پایدار و مطمئن\n\n"
        "از منوی زیر می‌تونی سرویس‌ها، "
        "حساب کاربری و پشتیبانی رو مدیریت کنی.\n\n"
        "💙 NovaLinkVPN\n"
        "FAST • STABLE • GLOBAL",
        reply_markup=main_menu(),
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "buy":
        text = (
            "🛒 خرید سرویس\n\n"
            "سرویس‌های NovaLinkVPN به‌زودی "
            "در این قسمت قرار می‌گیرن. 🚀"
        )

    elif query.data == "account":
        text = (
            "👤 حساب کاربری\n\n"
            f"🆔 شناسه تلگرام: {query.from_user.id}\n\n"
            "مدیریت کامل حساب در مراحل بعدی اضافه می‌شه."
        )

    elif query.data == "services":
        text = (
            "📦 سرویس‌های من\n\n"
            "در حال حاضر سرویسی برای این حساب ثبت نشده."
        )

    elif query.data == "support":
        text = (
            "🎫 پشتیبانی\n\n"
            "سیستم پشتیبانی NovaLinkVPN "
            "به‌زودی فعال می‌شه. 💙"
        )

    else:
        text = "❌ گزینه نامعتبر است."

    keyboard = [
        [
            InlineKeyboardButton(
                "🔙 منوی اصلی",
                callback_data="home"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def home_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🚀 NovaLinkVPN\n\n"
        "به منوی اصلی خوش اومدی. 🌐\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=main_menu(),
    )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("OK")


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not set.")

    if not RENDER_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL is not set.")

    webhook_url = f"{RENDER_URL}/telegram"

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("health", health)
    )

    application.add_handler(
        CallbackQueryHandler(
            home_handler,
            pattern="^home$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print(f"Starting NovaLinkVPN Bot")
    print(f"Webhook: {webhook_url}")
    print(f"Port: {PORT}")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
