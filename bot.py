import os
import asyncio
import logging

from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://novalinkvpn-bot.onrender.com"
)

WEBHOOK_PATH = "/telegram"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}"

CHANNEL_URL = "https://t.me/NovaLinkNETPN"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# =========================================================
# BOT APPLICATION
# =========================================================

application = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)

# =========================================================
# USER MENU
# =========================================================

def user_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop"),
            InlineKeyboardButton("📦 سرویس‌های من", callback_data="my_services"),
        ],
        [
            InlineKeyboardButton("👤 حساب کاربری", callback_data="account"),
            InlineKeyboardButton("💳 کیف پول", callback_data="wallet"),
        ],
        [
            InlineKeyboardButton("🎁 کد تخفیف", callback_data="discount"),
            InlineKeyboardButton("⭐ دعوت دوستان", callback_data="referral"),
        ],
        [
            InlineKeyboardButton("🎫 پشتیبانی", callback_data="support"),
            InlineKeyboardButton("📢 کانال", callback_data="channel"),
        ],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 داشبورد", callback_data="admin_dashboard"),
            InlineKeyboardButton("👥 کاربران", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("📦 سرویس‌ها", callback_data="admin_services"),
            InlineKeyboardButton("🛒 سفارش‌ها", callback_data="admin_orders"),
        ],
        [
            InlineKeyboardButton("💳 تراکنش‌ها", callback_data="admin_transactions"),
            InlineKeyboardButton("🎁 تخفیف‌ها", callback_data="admin_discounts"),
        ],
        [
            InlineKeyboardButton("⭐ سیستم دعوت", callback_data="admin_referral"),
            InlineKeyboardButton("🎫 پشتیبانی", callback_data="admin_support"),
        ],
        [
            InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin_broadcast"),
            InlineKeyboardButton("📈 گزارش‌ها", callback_data="admin_reports"),
        ],
        [
            InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings"),
        ],
    ])


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    text = f"""
🚀 سلام {user.first_name}!

به NovaLinkVPN خوش اومدی 💙

از منوی زیر می‌تونی سرویس موردنظرت رو انتخاب کنی.

⚡ سریع
🛡️ پایدار
🌍 جهانی

NovaLinkVPN
FAST • STABLE • GLOBAL
"""

    await update.message.reply_text(
        text,
        reply_markup=user_keyboard()
    )


# =========================================================
# /ADMIN
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ شما دسترسی مدیریت ندارید."
        )
        return

    await update.message.reply_text(
        "👑 پنل مدیریت NovaLinkVPN\n\n"
        "به پنل مدیریت خوش آمدید.",
        reply_markup=admin_keyboard()
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    # -----------------------------------------------------
    # USER
    # -----------------------------------------------------

    if data == "shop":

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚡ سرویس عادی",
                    callback_data="plan_normal"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎮 سرویس گیمینگ",
                    callback_data="plan_gaming"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚀 سرویس پرسرعت",
                    callback_data="plan_fast"
                )
            ],
            [
                InlineKeyboardButton(
                    "💎 سرویس VIP",
                    callback_data="plan_vip"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="home"
                )
            ],
        ])

        await query.edit_message_text(
            "🛒 فروشگاه NovaLinkVPN\n\n"
            "نوع سرویس موردنظرت رو انتخاب کن:",
            reply_markup=keyboard
        )

    elif data.startswith("plan_"):

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📅 ۱ ماهه",
                    callback_data=f"buy_{data}_1"
                ),
                InlineKeyboardButton(
                    "📅 ۲ ماهه",
                    callback_data=f"buy_{data}_2"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📅 ۳ ماهه",
                    callback_data=f"buy_{data}_3"
                ),
                InlineKeyboardButton(
                    "📅 ۶ ماهه",
                    callback_data=f"buy_{data}_6"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📅 ۱۲ ماهه",
                    callback_data=f"buy_{data}_12"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="shop"
                )
            ],
        ])

        await query.edit_message_text(
            "📦 انتخاب مدت سرویس\n\n"
            "مدت موردنظرت رو انتخاب کن:",
            reply_markup=keyboard
        )

    elif data.startswith("buy_"):

        await query.edit_message_text(
            "🛒 ثبت سفارش\n\n"
            "⚠️ سیستم پرداخت هنوز در حال آماده‌سازی است.\n\n"
            "به‌زودی امکان خرید آنلاین فعال می‌شود.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 فروشگاه",
                        callback_data="shop"
                    )
                ]
            ])
        )

    elif data == "my_services":

        await query.edit_message_text(
            "📦 سرویس‌های من\n\n"
            "در حال حاضر سرویسی برای نمایش ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛒 خرید سرویس",
                        callback_data="shop"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "account":

        await query.edit_message_text(
            f"""
👤 حساب کاربری

🆔 Telegram ID:
{user.id}

👤 نام:
{user.first_name}

💳 موجودی کیف پول:
0 تومان

📦 سرویس فعال:
0

⭐ امتیاز:
0
""",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 کیف پول",
                        callback_data="wallet"
                    ),
                    InlineKeyboardButton(
                        "📜 تراکنش‌ها",
                        callback_data="transactions"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "wallet":

        await query.edit_message_text(
            "💳 کیف پول\n\n"
            "موجودی فعلی:\n"
            "0 تومان\n\n"
            "سیستم افزایش موجودی به‌زودی فعال می‌شود.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📜 تراکنش‌ها",
                        callback_data="transactions"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "transactions":

        await query.edit_message_text(
            "📜 تراکنش‌ها\n\n"
            "هنوز تراکنشی ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="account"
                    )
                ]
            ])
        )

    elif data == "discount":

        await query.edit_message_text(
            "🎁 کد تخفیف\n\n"
            "کد تخفیف خودت رو برای استفاده از تخفیف وارد کن.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "referral":

        bot_username = context.bot.username

        referral_link = (
            f"https://t.me/{bot_username}?start=ref_{user.id}"
        )

        await query.edit_message_text(
            f"""
⭐ دعوت دوستان

لینک دعوت اختصاصی شما:

{referral_link}

با دعوت دوستان می‌تونی از پاداش‌های NovaLinkVPN استفاده کنی. 🎁
""",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "support":

        await query.edit_message_text(
            "🎫 پشتیبانی NovaLinkVPN\n\n"
            "برای ارتباط با پشتیبانی، درخواست خودت رو ارسال کن.\n\n"
            "سیستم تیکت به‌زودی فعال می‌شود.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "channel":

        await query.edit_message_text(
            "📢 کانال رسمی NovaLinkVPN\n\n"
            "برای مشاهده سرویس‌ها و اطلاعیه‌ها وارد کانال شو:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🌐 ورود به کانال",
                        url=CHANNEL_URL
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif data == "home":

        await query.edit_message_text(
            "🏠 منوی اصلی NovaLinkVPN\n\n"
            "گزینه موردنظرت رو انتخاب کن:",
            reply_markup=user_keyboard()
        )

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    elif data.startswith("admin_"):

        if user.id != ADMIN_ID:

            await query.edit_message_text(
                "⛔ دسترسی غیرمجاز."
            )
            return

        if data == "admin_dashboard":

            await query.edit_message_text(
                """
📊 داشبورد مدیریت

👥 کاربران: 0
🟢 کاربران فعال: 0
📦 سرویس‌های فعال: 0

🛒 فروش امروز: 0
💰 درآمد امروز: 0 تومان
💰 درآمد ماه: 0 تومان

🎫 تیکت باز: 0
""",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_users":

            await query.edit_message_text(
                "👥 مدیریت کاربران\n\n"
                "هنوز کاربر قابل نمایش ثبت نشده است.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_services":

            await query.edit_message_text(
                "📦 مدیریت سرویس‌ها\n\n"
                "از این بخش می‌توان سرویس‌ها و پلن‌ها را مدیریت کرد.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "➕ افزودن سرویس",
                            callback_data="admin_add_service"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_orders":

            await query.edit_message_text(
                "🛒 مدیریت سفارش‌ها\n\n"
                "سفارشی ثبت نشده است.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_transactions":

            await query.edit_message_text(
                "💳 تراکنش‌ها\n\n"
                "تراکنشی ثبت نشده است.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_discounts":

            await query.edit_message_text(
                "🎁 مدیریت کدهای تخفیف\n\n"
                "کد تخفیفی ساخته نشده است.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "➕ ساخت کد تخفیف",
                            callback_data="admin_add_discount"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_referral":

            await query.edit_message_text(
                "⭐ سیستم دعوت دوستان\n\n"
                "مدیریت دعوت‌ها و پاداش‌ها از این بخش انجام می‌شود.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_support":

            await query.edit_message_text(
                "🎫 مدیریت پشتیبانی\n\n"
                "تیکت بازی وجود ندارد.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_broadcast":

            await query.edit_message_text(
                "📢 ارسال همگانی\n\n"
                "سیستم ارسال همگانی آماده توسعه است.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_reports":

            await query.edit_message_text(
                "📈 گزارش‌ها\n\n"
                "گزارش‌های فروش، کاربران و سرویس‌ها در این بخش قرار می‌گیرند.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_settings":

            await query.edit_message_text(
                "⚙️ تنظیمات\n\n"
                "تنظیمات اصلی NovaLinkVPN.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔙 پنل مدیریت",
                            callback_data="admin_home"
                        )
                    ]
                ])
            )

        elif data == "admin_home":

            await query.edit_message_text(
                "👑 پنل مدیریت NovaLinkVPN",
                reply_markup=admin_keyboard()
            )


# =========================================================
# HTTP HEALTH CHECK
# =========================================================

async def health(request):

    return web.Response(
        text="OK",
        status=200,
        content_type="text/plain"
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

async def telegram_webhook(request):

    try:

        data = await request.json()

        update = Update.de_json(
            data,
            application.bot
        )

        await application.process_update(update)

        return web.Response(
            text="OK",
            status=200
        )

    except Exception as e:

        logger.exception(
            "Webhook error: %s",
            e
        )

        return web.Response(
            text="ERROR",
            status=500
        )


# =========================================================
# ROOT
# =========================================================

async def root(request):

    return web.Response(
        text="NovaLinkVPN Bot is running.",
        status=200
    )


# =========================================================
# START SERVER
# =========================================================

async def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    logger.info(
        "Starting NovaLinkVPN Bot..."
    )

    # Initialize Telegram application
    await application.initialize()

    await application.start()

    # Set Telegram webhook
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True
    )

    logger.info(
        "Webhook set to: %s",
        WEBHOOK_URL
    )

    # Create HTTP server
    server = web.Application()

    server.router.add_get(
        "/",
        root
    )

    server.router.add_get(
        "/health",
        health
    )

    server.router.add_post(
        WEBHOOK_PATH,
        telegram_webhook
    )

    runner = web.AppRunner(server)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    logger.info(
        "HTTP server running on port %s",
        PORT
    )

    logger.info(
        "Health endpoint: %s/health",
        RENDER_URL
    )

    # Keep server alive
    try:

        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, asyncio.CancelledError):

        pass

    finally:

        await runner.cleanup()

        await application.stop()

        await application.shutdown()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
