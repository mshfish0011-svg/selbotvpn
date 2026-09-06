import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🚀 به NovaLinkVPN خوش اومدی\n\n"
        "🌐 اتصال سریع، پایدار و مطمئن\n\n"
        "از منوی زیر می‌تونی سرویس‌ها رو ببینی، "
        "حساب کاربری و سرویس‌های خودت رو مدیریت کنی "
        "و در صورت نیاز با پشتیبانی در ارتباط باشی.\n\n"
        "💙 NovaLinkVPN\n"
        "FAST • STABLE • GLOBAL",
        reply_markup=reply_markup,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy":
        text = (
            "🛒 خرید سرویس\n\n"
            "در حال آماده‌سازی سرویس‌ها هستیم... 🚀\n\n"
            "به‌زودی پلن‌های NovaLinkVPN "
            "اینجا نمایش داده می‌شن."
        )

    elif query.data == "account":
        text = (
            "👤 حساب کاربری\n\n"
            f"🆔 شناسه شما: {query.from_user.id}\n\n"
            "اطلاعات کامل حساب در نسخه بعدی اضافه می‌شه."
        )

    elif query.data == "services":
        text = (
            "📦 سرویس‌های من\n\n"
            "هنوز سرویسی برای این حساب ثبت نشده."
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
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="home")]
    ]

    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def home_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

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

    await query.edit_message_text(
        "🚀 NovaLinkVPN\n\n"
        "به منوی اصلی خوش اومدی. 🌐\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is not set."
        )

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(home_handler, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("NovaLinkVPN Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
