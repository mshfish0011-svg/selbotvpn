import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# NOVALINKVPN BOT
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

CHANNEL_URL = "https://t.me/NovaLinkNETPN"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# USER MAIN MENU
# ============================================================

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop"),
            InlineKeyboardButton("📦 سرویس‌های من", callback_data="services"),
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
    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# ADMIN MENU
# ============================================================

def admin_menu():
    keyboard = [
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
        [
            InlineKeyboardButton("🏠 منوی کاربر", callback_data="home"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user:
        return

    # اگر مدیر باشد
    if user.id == ADMIN_ID:
        text = (
            "👑 خوش اومدی مدیر NovaLinkVPN\n\n"
            "پنل مدیریت با موفقیت شناسایی شد.\n\n"
            "از این قسمت می‌تونی تمام بخش‌های ربات "
            "رو مدیریت کنی.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🚀 NOVALINKVPN ADMIN"
        )

        await update.message.reply_text(
            text,
            reply_markup=admin_menu(),
        )

        return

    # کاربر عادی
    text = (
        "🚀 به NovaLinkVPN خوش اومدی\n\n"
        "🌐 FAST • STABLE • GLOBAL\n\n"
        "از منوی زیر می‌تونی سرویس موردنظرت رو "
        "انتخاب و حساب خودت رو مدیریت کنی.\n\n"
        "💙 امیدواریم تجربه خوبی با NovaLinkVPN داشته باشی."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
    )


# ============================================================
# USER CALLBACKS
# ============================================================

async def user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = query.data

    # --------------------------------------------------------
    # HOME
    # --------------------------------------------------------

    if data == "home":

        await query.edit_message_text(
            "🚀 NovaLinkVPN\n\n"
            "به منوی اصلی خوش اومدی. 🌐\n\n"
            "یکی از گزینه‌های زیر رو انتخاب کن:",
            reply_markup=main_menu(),
        )

        return

    # --------------------------------------------------------
    # SHOP
    # --------------------------------------------------------

    if data == "shop":

        keyboard = [
            [
                InlineKeyboardButton(
                    "🌐 سرویس‌های عادی",
                    callback_data="shop_normal",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎮 سرویس گیم",
                    callback_data="shop_gaming",
                )
            ],
            [
                InlineKeyboardButton(
                    "🚀 سرویس پرسرعت",
                    callback_data="shop_fast",
                )
            ],
            [
                InlineKeyboardButton(
                    "💎 سرویس VIP",
                    callback_data="shop_vip",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔥 پیشنهادهای ویژه",
                    callback_data="shop_special",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 بازگشت",
                    callback_data="home",
                )
            ],
        ]

        await query.edit_message_text(
            "🛒 خرید سرویس\n\n"
            "نوع سرویس موردنظرت رو انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return

    # --------------------------------------------------------
    # SERVICE CATEGORIES
    # --------------------------------------------------------

    if data.startswith("shop_"):

        await query.edit_message_text(
            "📦 این بخش در حال آماده‌سازی است.\n\n"
            "به‌زودی پلن‌ها، قیمت‌ها، حجم و مدت سرویس "
            "در این قسمت نمایش داده می‌شوند.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت به خرید",
                        callback_data="shop",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 منوی اصلی",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # MY SERVICES
    # --------------------------------------------------------

    if data == "services":

        await query.edit_message_text(
            "📦 سرویس‌های من\n\n"
            "در حال حاضر سرویسی برای این حساب ثبت نشده.\n\n"
            "پس از خرید، سرویس‌های فعال شما "
            "در این قسمت نمایش داده خواهند شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛒 خرید سرویس",
                        callback_data="shop",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # ACCOUNT
    # --------------------------------------------------------

    if data == "account":

        user = query.from_user

        await query.edit_message_text(
            "👤 حساب کاربری\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Telegram ID: {user.id}\n"
            f"👤 نام: {user.first_name}\n\n"
            "📦 سرویس فعال: 0\n"
            "💳 موجودی: 0 تومان\n"
            "⭐ امتیاز: 0\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "تاریخچه و اطلاعات کامل حساب "
            "در نسخه دیتابیس اضافه خواهد شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 کیف پول",
                        callback_data="wallet",
                    ),
                    InlineKeyboardButton(
                        "📜 تراکنش‌ها",
                        callback_data="transactions",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # WALLET
    # --------------------------------------------------------

    if data == "wallet":

        await query.edit_message_text(
            "💳 کیف پول\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💰 موجودی فعلی:\n"
            "0 تومان\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "افزایش موجودی و تاریخچه تراکنش‌ها "
            "در مرحله پرداخت فعال خواهند شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ افزایش موجودی",
                        callback_data="wallet_add",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📜 تراکنش‌ها",
                        callback_data="transactions",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # WALLET ADD
    # --------------------------------------------------------

    if data == "wallet_add":

        await query.edit_message_text(
            "➕ افزایش موجودی\n\n"
            "سیستم پرداخت آنلاین در مرحله بعدی "
            "به ربات متصل خواهد شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="wallet",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if data == "transactions":

        await query.edit_message_text(
            "📜 تاریخچه تراکنش‌ها\n\n"
            "هنوز تراکنشی ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="account",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # DISCOUNT
    # --------------------------------------------------------

    if data == "discount":

        await query.edit_message_text(
            "🎁 کد تخفیف\n\n"
            "اگر کد تخفیف داری، در نسخه بعدی "
            "می‌تونی اینجا واردش کنی.\n\n"
            "مثال:\n"
            "NOVALINK20",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # REFERRAL
    # --------------------------------------------------------

    if data == "referral":

        await query.edit_message_text(
            "⭐ دعوت دوستان\n\n"
            "دوستانت رو به NovaLinkVPN دعوت کن "
            "و از سیستم پاداش استفاده کن.\n\n"
            "👥 دعوت‌شده‌ها: 0\n"
            "💰 درآمد: 0 تومان\n\n"
            "🔗 لینک دعوت اختصاصی پس از فعال شدن "
            "سیستم Referral ساخته خواهد شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📤 اشتراک‌گذاری",
                        callback_data="referral_share",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # SUPPORT
    # --------------------------------------------------------

    if data == "support":

        await query.edit_message_text(
            "🎫 پشتیبانی NovaLinkVPN\n\n"
            "چه مشکلی داری؟\n\n"
            "🔧 مشکل اتصال\n"
            "🐌 مشکل سرعت\n"
            "💳 مشکل پرداخت\n"
            "📦 مشکل سرویس\n"
            "❓ سایر موارد\n\n"
            "سیستم تیکت در مرحله بعدی فعال خواهد شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🆕 ایجاد تیکت",
                        callback_data="new_ticket",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    if data == "channel":

        await query.edit_message_text(
            "📢 کانال رسمی NovaLinkVPN\n\n"
            "برای اطلاع از سرویس‌ها، اخبار، "
            "تخفیف‌ها و اطلاعیه‌ها عضو کانال شو. 🚀",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📢 ورود به کانال",
                        url=CHANNEL_URL,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="home",
                    )
                ],
            ]),
        )

        return

    # --------------------------------------------------------
    # DEFAULT
    # --------------------------------------------------------

    await query.edit_message_text(
        "❌ این بخش هنوز فعال نشده.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏠 منوی اصلی",
                    callback_data="home",
                )
            ]
        ]),
    )


# ============================================================
# ADMIN CALLBACKS
# ============================================================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    # امنیت: فقط Owner
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "⛔ دسترسی غیرمجاز",
            show_alert=True,
        )
        return

    await query.answer()

    data = query.data

    # --------------------------------------------------------
    # ADMIN HOME
    # --------------------------------------------------------

    if data == "admin_home":

        await query.edit_message_text(
            "👑 NOVALINKVPN ADMIN\n\n"
            "پنل مدیریت اصلی:",
            reply_markup=admin_menu(),
        )

        return

    # --------------------------------------------------------
    # DASHBOARD
    # --------------------------------------------------------

    if data == "admin_dashboard":

        await query.edit_message_text(
            "📊 داشبورد مدیریت\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "👥 کاربران: 0\n"
            "🟢 کاربران فعال: 0\n"
            "📦 سرویس‌های فعال: 0\n"
            "🛒 فروش امروز: 0\n"
            "💰 درآمد امروز: 0 تومان\n"
            "🎫 تیکت‌های باز: 0\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "آمار واقعی پس از اتصال دیتابیس "
            "نمایش داده خواهد شد.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    if data == "admin_users":

        await query.edit_message_text(
            "👥 مدیریت کاربران\n\n"
            "🔎 جستجوی کاربر\n"
            "📋 لیست کاربران\n"
            "🟢 کاربران فعال\n"
            "🔴 کاربران مسدود\n"
            "🆕 کاربران جدید",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # SERVICES
    # --------------------------------------------------------

    if data == "admin_services":

        await query.edit_message_text(
            "📦 مدیریت سرویس‌ها\n\n"
            "➕ ایجاد سرویس\n"
            "✏️ ویرایش سرویس\n"
            "🗑 حذف سرویس\n\n"
            "📊 سرویس‌های فعال\n"
            "⏳ سرویس‌های منقضی\n"
            "🚨 نزدیک انقضا",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # ORDERS
    # --------------------------------------------------------

    if data == "admin_orders":

        await query.edit_message_text(
            "🛒 مدیریت سفارش‌ها\n\n"
            "🟡 در انتظار پرداخت\n"
            "🟢 پرداخت‌شده\n"
            "🔴 ناموفق\n"
            "↩️ بازگشت وجه\n\n"
            "📊 گزارش فروش",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if data == "admin_transactions":

        await query.edit_message_text(
            "💳 مدیریت تراکنش‌ها\n\n"
            "💰 پرداخت‌ها\n"
            "➕ افزایش موجودی\n"
            "↩️ Refund\n"
            "❌ تراکنش‌های ناموفق\n\n"
            "📊 گزارش مالی",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # DISCOUNTS
    # --------------------------------------------------------

    if data == "admin_discounts":

        await query.edit_message_text(
            "🎁 مدیریت تخفیف\n\n"
            "➕ ساخت کد تخفیف\n"
            "📋 کدهای فعال\n"
            "⏳ کدهای منقضی\n"
            "📊 آمار استفاده",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # REFERRAL
    # --------------------------------------------------------

    if data == "admin_referral":

        await query.edit_message_text(
            "⭐ مدیریت سیستم دعوت\n\n"
            "👥 تعداد دعوت‌ها\n"
            "💰 پاداش‌ها\n"
            "📊 گزارش Referral\n"
            "⚙️ تنظیمات پاداش",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # SUPPORT
    # --------------------------------------------------------

    if data == "admin_support":

        await query.edit_message_text(
            "🎫 مدیریت پشتیبانی\n\n"
            "🔴 تیکت‌های جدید\n"
            "🟡 در حال بررسی\n"
            "🟢 بسته‌شده\n\n"
            "📋 مشاهده تیکت‌ها",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # BROADCAST
    # --------------------------------------------------------

    if data == "admin_broadcast":

        await query.edit_message_text(
            "📢 ارسال همگانی\n\n"
            "👥 همه کاربران\n"
            "🟢 کاربران فعال\n"
            "📦 دارندگان سرویس\n"
            "⏳ نزدیک انقضا\n\n"
            "✏️ نوشتن پیام\n"
            "📎 ارسال رسانه\n"
            "⏰ زمان‌بندی",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # REPORTS
    # --------------------------------------------------------

    if data == "admin_reports":

        await query.edit_message_text(
            "📈 گزارش‌ها\n\n"
            "📊 گزارش فروش\n"
            "👥 گزارش کاربران\n"
            "💰 گزارش درآمد\n"
            "📦 گزارش سرویس‌ها\n"
            "⭐ گزارش Referral",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

    if data == "admin_settings":

        await query.edit_message_text(
            "⚙️ تنظیمات NovaLinkVPN\n\n"
            "🏷 اطلاعات برند\n"
            "💰 قیمت‌ها\n"
            "💳 پرداخت\n"
            "📦 سرویس‌ها\n"
            "🎁 تخفیف‌ها\n"
            "⭐ Referral\n"
            "🎫 پشتیبانی\n"
            "📢 کانال\n"
            "🔐 امنیت",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 پنل مدیریت",
                        callback_data="admin_home",
                    )
                ]
            ]),
        )

        return

    # --------------------------------------------------------
    # USER MENU FROM ADMIN
    # --------------------------------------------------------

    if data == "home":

        await query.edit_message_text(
            "🚀 NovaLinkVPN\n\n"
            "منوی کاربر:",
            reply_markup=main_menu(),
        )

        return


# ============================================================
# ADMIN COMMAND
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user or user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ شما دسترسی به پنل مدیریت ندارید."
        )
        return

    await update.message.reply_text(
        "👑 پنل مدیریت NovaLinkVPN",
        reply_markup=admin_menu(),
    )


# ============================================================
# HEALTH CHECK
# ============================================================

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("OK")


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is not set."
        )

    if not ADMIN_ID:
        logger.warning(
            "ADMIN_ID is not set. Admin panel will be unavailable."
        )

    if not RENDER_URL:
        raise RuntimeError(
            "RENDER_EXTERNAL_URL environment variable is not set."
        )

    webhook_url = f"{RENDER_URL}/telegram"

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CommandHandler("health", health)
    )

    # Admin callbacks first
    admin_patterns = (
        "^admin_"
        "|^admin_home$"
    )

    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=admin_patterns,
        )
    )

    # User callbacks
    application.add_handler(
        CallbackQueryHandler(
            user_callback
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Starting NovaLinkVPN Bot")
    logger.info("Webhook: %s", webhook_url)
    logger.info("Port: %s", PORT)

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
