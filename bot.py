import os
import json
import asyncio
import logging
import secrets
from copy import deepcopy
from datetime import datetime, timezone, timedelta

from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# NovaLinkVPN Telegram Bot - Single File Edition
# No SQL database. Uses JSON file + Backup/Restore.
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
PORT = int(os.getenv("PORT", "10000") or "10000")

RENDER_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://novalinkvpn-bot.onrender.com",
).rstrip("/")

WEBHOOK_PATH = "/telegram"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}"

CHANNEL_URL = "https://t.me/NovaLinkNETPN"
BRAND = "NovaLinkVPN"

DATA_FILE = "data.json"
BACKUP_DIR = "backups"
AUTO_BACKUP_HOURS = 6
MAX_BACKUPS = 20

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("novalinkvpn")

# Telegram application instance shared with the webhook handler.
application = None


# =========================================================
# TIME / HELPERS
# =========================================================

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def money(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def uid(prefix="id"):
    return f"{prefix}_{secrets.token_hex(6)}"


def esc(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# =========================================================
# DEFAULT JSON DATABASE
# =========================================================

def default_data():
    return {
        "meta": {
            "version": 1,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
        "settings": {
            "brand": BRAND,
            "channel_url": CHANNEL_URL,
            "support_username": "",
            "currency": "تومان",
            "maintenance": False,
            "welcome_enabled": True,
            "auto_expiry_notice": True,
            "expiry_notice_days": [7, 3, 1],
            "referral_reward": 0,
            "min_topup": 0,
            "ticket_retention_hours": 72,
            "auto_backup_enabled": True,
            "loyalty_enabled": True,
            "loyalty_rate": 1,
            "max_open_tickets_per_user": 3,
        },
        "users": {},
        "services": {},
        "orders": {},
        "transactions": {},
        "discounts": {},
        "referrals": {},
        "tickets": {},
        "broadcasts": {},
        "audit_logs": {},
        "notifications": {},
        "admins": {
            str(ADMIN_ID): {
                "role": "owner",
                "active": True,
                "created_at": now_iso(),
            }
        },
        "stats": {
            "total_sales": 0,
            "total_revenue": 0,
        },
    }


data = default_data()
data_lock = asyncio.Lock()


# =========================================================
# DATABASE JSON OPERATIONS
# =========================================================

def normalize_data():
    global data

    base = default_data()

    for key, value in base.items():
        if key not in data:
            data[key] = deepcopy(value)

    for key in [
        "users",
        "services",
        "orders",
        "transactions",
        "discounts",
        "referrals",
        "tickets",
        "broadcasts",
        "audit_logs",
        "notifications",
    ]:
        if not isinstance(data.get(key), dict):
            data[key] = {}

    if not isinstance(data.get("admins"), dict):
        data["admins"] = {}

    if ADMIN_ID:
        data["admins"][str(ADMIN_ID)] = {
            "role": "owner",
            "active": True,
            "created_at": data["admins"].get(
                str(ADMIN_ID), {}
            ).get("created_at", now_iso()),
        }


def load_data():
    global data

    if not os.path.exists(DATA_FILE):
        data = default_data()
        save_data_sync()
        return

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        normalize_data()
        save_data_sync()
    except Exception:
        logger.exception("Could not load data.json. Creating a fresh file.")
        data = default_data()
        save_data_sync()


def save_data_sync():
    data["meta"]["updated_at"] = now_iso()

    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp, DATA_FILE)


async def save_data():
    async with data_lock:
        save_data_sync()


async def make_backup(reason="manual"):
    os.makedirs(BACKUP_DIR, exist_ok=True)

    await save_data()

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(
        BACKUP_DIR,
        f"novalinkvpn_backup_{stamp}_{reason}.json",
    )

    async with data_lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    backups = sorted(
        [
            os.path.join(BACKUP_DIR, x)
            for x in os.listdir(BACKUP_DIR)
            if x.endswith(".json")
        ],
        key=os.path.getmtime,
        reverse=True,
    )

    for old in backups[MAX_BACKUPS:]:
        try:
            os.remove(old)
        except OSError:
            pass

    return path


def restore_from_bytes(raw):
    restored = json.loads(raw.decode("utf-8"))

    required = [
        "meta",
        "settings",
        "users",
        "services",
        "orders",
        "transactions",
        "discounts",
        "referrals",
        "tickets",
        "broadcasts",
        "admins",
    ]

    for key in required:
        if key not in restored:
            raise ValueError(f"Missing key: {key}")

    if not isinstance(restored["users"], dict):
        raise ValueError("Invalid users structure.")

    if not isinstance(restored["services"], dict):
        raise ValueError("Invalid services structure.")

    return restored


# =========================================================
# USER / ADMIN ACCESS
# =========================================================

def is_owner(user_id):
    return user_id == ADMIN_ID and ADMIN_ID != 0


def get_admin_role(user_id):
    record = data.get("admins", {}).get(str(user_id))
    if not record or not record.get("active"):
        return None
    return record.get("role")


def can_admin(user_id):
    return get_admin_role(user_id) in {"owner", "manager", "support", "finance"}


def can_manage_users(user_id):
    return get_admin_role(user_id) in {"owner", "manager"}


def can_manage_services(user_id):
    return get_admin_role(user_id) in {"owner", "manager"}


def can_manage_finance(user_id):
    return get_admin_role(user_id) in {"owner", "manager", "finance"}


def can_manage_support(user_id):
    return get_admin_role(user_id) in {"owner", "manager", "support"}


def can_broadcast(user_id):
    return get_admin_role(user_id) in {"owner", "manager"}


async def audit_action(admin_id, action, target="", details=""):
    """Keep a compact audit trail in JSON."""
    try:
        log_id = uid("audit")
        data.setdefault("audit_logs", {})[log_id] = {
            "id": log_id,
            "admin_id": int(admin_id),
            "action": str(action),
            "target": str(target),
            "details": str(details)[:1000],
            "created_at": now_iso(),
        }
        logs = data["audit_logs"]
        if len(logs) > 2000:
            keep = sorted(
                logs,
                key=lambda k: logs[k].get("created_at", ""),
                reverse=True,
            )[:2000]
            data["audit_logs"] = {k: logs[k] for k in keep}
        await save_data()
    except Exception:
        logger.exception("Audit log error.")


# =========================================================
# USER DATA
# =========================================================

async def ensure_user(tg_user):
    key = str(tg_user.id)

    if key not in data["users"]:
        data["users"][key] = {
            "id": tg_user.id,
            "first_name": tg_user.first_name or "",
            "last_name": tg_user.last_name or "",
            "username": tg_user.username or "",
            "created_at": now_iso(),
            "last_seen": now_iso(),
            "balance": 0,
            "blocked": False,
            "referral_code": secrets.token_hex(4).upper(),
            "referred_by": None,
            "points": 0,
            "service_ids": [],
            "order_ids": [],
            "transaction_ids": [],
            "ticket_ids": [],
            "expiry_notices": {},
        }
    else:
        u = data["users"][key]
        u["first_name"] = tg_user.first_name or u.get("first_name", "")
        u["last_name"] = tg_user.last_name or u.get("last_name", "")
        u["username"] = tg_user.username or u.get("username", "")
        u["last_seen"] = now_iso()

    await save_data()
    return data["users"][key]


# =========================================================
# KEYBOARDS
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


def admin_keyboard(role="owner"):
    rows = [
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
            InlineKeyboardButton("⭐ دعوت‌ها", callback_data="admin_referral"),
            InlineKeyboardButton("🎫 پشتیبانی", callback_data="admin_support"),
        ],
        [
            InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin_broadcast"),
            InlineKeyboardButton("📈 گزارش‌ها", callback_data="admin_reports"),
        ],
        [
            InlineKeyboardButton("💾 بکاپ / بازیابی", callback_data="admin_backup"),
            InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings"),
        ],
    ]

    if role == "owner":
        rows.append([
            InlineKeyboardButton("🛡️ دسترسی مدیران", callback_data="admin_staff"),
        ])

    return InlineKeyboardMarkup(rows)


def back_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")]
    ])


def back_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")]
    ])


# =========================================================
# START / HOME
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update.effective_user)

    if user.get("blocked"):
        await update.message.reply_text("⛔ دسترسی شما به این ربات مسدود شده است.")
        return

    await update.message.reply_text(
        f"🚀 سلام {esc(user['first_name'])}!\n\n"
        f"به {BRAND} خوش اومدی 💙\n\n"
        "از منوی زیر استفاده کن:",
        reply_markup=user_keyboard(),
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update.effective_user)

    role = get_admin_role(update.effective_user.id)
    if not role:
        await update.message.reply_text("⛔ شما دسترسی مدیریت ندارید.")
        return

    await update.message.reply_text(
        f"👑 پنل مدیریت {BRAND}\n\n"
        f"سطح دسترسی: {role}",
        reply_markup=admin_keyboard(role),
    )


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 Telegram ID شما:\n{update.effective_user.id}"
    )


# =========================================================
# USER SHOP
# =========================================================

def active_services():
    return [
        s for s in data["services"].values()
        if s.get("active", True)
    ]


def service_display(service):
    return (
        f"📦 {esc(service.get('name', 'بدون نام'))}\n"
        f"💾 حجم: {service.get('traffic_gb', 0)} GB\n"
        f"🌍 سرور: {esc(service.get('server', '-'))}\n"
        f"⏳ مدت: {service.get('duration_days', 0)} روز\n"
        f"💰 قیمت: {money(service.get('price', 0))} تومان"
    )


async def show_shop(query):
    services = active_services()

    if not services:
        await query.edit_message_text(
            "🛒 فروشگاه\n\n"
            "در حال حاضر هیچ پلن فعالی ثبت نشده است.\n"
            "ادمین می‌تواند از پنل مدیریت پلن اضافه کند.",
            reply_markup=back_home(),
        )
        return

    rows = []
    for service in services[:40]:
        rows.append([
            InlineKeyboardButton(
                f"📦 {service.get('name', 'پلن')}",
                callback_data=f"view_service:{service['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")
    ])

    await query.edit_message_text(
        "🛒 فروشگاه NovaLinkVPN\n\n"
        "یک سرویس را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# =========================================================
# PURCHASE
# =========================================================

async def view_service(query, service_id):
    service = data["services"].get(service_id)

    if not service or not service.get("active", True):
        await query.edit_message_text(
            "❌ این سرویس در دسترس نیست.",
            reply_markup=back_home(),
        )
        return

    text = (
        f"📦 {esc(service.get('name', ''))}\n\n"
        f"💾 حجم: {service.get('traffic_gb', 0)} GB\n"
        f"🌍 سرور: {esc(service.get('server', '-'))}\n"
        f"⏳ مدت: {service.get('duration_days', 0)} روز\n"
        f"💰 قیمت: {money(service.get('price', 0))} تومان\n\n"
        f"ℹ️ {esc(service.get('description', ''))}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💳 خرید با کیف پول",
                callback_data=f"buy_wallet:{service_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 فروشگاه",
                callback_data="shop",
            )
        ],
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


async def buy_with_wallet(query, user_id, service_id):
    user = data["users"].get(str(user_id))
    service = data["services"].get(service_id)

    if not user or not service or not service.get("active", True):
        await query.edit_message_text(
            "❌ سرویس قابل خرید نیست.",
            reply_markup=back_home(),
        )
        return

    price = int(service.get("price", 0))

    if user.get("balance", 0) < price:
        await query.edit_message_text(
            f"❌ موجودی کافی نیست.\n\n"
            f"قیمت: {money(price)} تومان\n"
            f"موجودی: {money(user.get('balance', 0))} تومان",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 کیف پول",
                        callback_data="wallet",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 فروشگاه",
                        callback_data="shop",
                    )
                ],
            ]),
        )
        return

    user["balance"] -= price

    order_id = uid("ord")
    service_id_owned = uid("svc")

    expires = datetime.now(timezone.utc) + timedelta(
        days=int(service.get("duration_days", 30))
    )

    owned = {
        "id": service_id_owned,
        "user_id": user_id,
        "plan_id": service_id,
        "plan_name": service.get("name", ""),
        "traffic_gb": service.get("traffic_gb", 0),
        "server": service.get("server", ""),
        "config": "",
        "created_at": now_iso(),
        "expires_at": expires.isoformat(),
        "status": "active",
        "traffic_used_gb": 0,
    }

    order = {
        "id": order_id,
        "user_id": user_id,
        "service_id": service_id,
        "amount": price,
        "status": "paid",
        "created_at": now_iso(),
        "payment_method": "wallet",
    }

    tx_id = uid("tx")
    transaction = {
        "id": tx_id,
        "user_id": user_id,
        "type": "purchase",
        "amount": -price,
        "description": f"خرید {service.get('name', '')}",
        "created_at": now_iso(),
        "reference": order_id,
    }

    data["services"][service_id_owned] = owned
    data["orders"][order_id] = order
    data["transactions"][tx_id] = transaction

    user["service_ids"].append(service_id_owned)
    user["order_ids"].append(order_id)
    user["transaction_ids"].append(tx_id)

    data["stats"]["total_sales"] += 1
    data["stats"]["total_revenue"] += price

    await save_data()

    await query.edit_message_text(
        "✅ خرید با موفقیت انجام شد!\n\n"
        f"📦 سرویس: {esc(service.get('name', ''))}\n"
        f"💰 مبلغ: {money(price)} تومان\n"
        f"⏳ انقضا: {expires.strftime('%Y-%m-%d')}\n\n"
        "⚠️ کانفیگ هنوز توسط ادمین برای این سرویس وارد نشده است.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📦 سرویس‌های من",
                    callback_data="my_services",
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


# =========================================================
# USER SERVICES
# =========================================================

async def show_my_services(query, user_id):
    user = data["users"].get(str(user_id))
    if not user:
        await query.edit_message_text("❌ کاربر پیدا نشد.", reply_markup=back_home())
        return

    services = [
        data["services"].get(sid)
        for sid in user.get("service_ids", [])
        if data["services"].get(sid)
    ]

    if not services:
        await query.edit_message_text(
            "📦 سرویس‌های من\n\n"
            "هنوز سرویسی نداری.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop")],
                [InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")],
            ]),
        )
        return

    rows = []
    for s in services[-30:]:
        status = "🟢" if s.get("status") == "active" else "🔴"
        rows.append([
            InlineKeyboardButton(
                f"{status} {s.get('plan_name', 'سرویس')}",
                callback_data=f"owned_service:{s['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")
    ])

    await query.edit_message_text(
        "📦 سرویس‌های من\n\n"
        "سرویس موردنظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def show_owned_service(query, service_id):
    service = data["services"].get(service_id)

    if not service:
        await query.edit_message_text(
            "❌ سرویس پیدا نشد.",
            reply_markup=back_home(),
        )
        return

    expires = parse_dt(service.get("expires_at"))
    expiry_text = expires.strftime("%Y-%m-%d %H:%M") if expires else "-"

    await query.edit_message_text(
        f"📦 {esc(service.get('plan_name', 'سرویس'))}\n\n"
        f"📡 وضعیت: {service.get('status', '-')}\n"
        f"💾 حجم: {service.get('traffic_gb', 0)} GB\n"
        f"📊 مصرف: {service.get('traffic_used_gb', 0)} GB\n"
        f"🌍 سرور: {esc(service.get('server', '-'))}\n"
        f"⏳ انقضا: {expiry_text}\n\n"
        f"🔑 کانفیگ:\n{esc(service.get('config') or 'هنوز ثبت نشده')}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 تمدید",
                    callback_data=f"renew:{service_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "📦 سرویس‌های من",
                    callback_data="my_services",
                )
            ],
        ]),
    )


# =========================================================
# ACCOUNT / WALLET
# =========================================================

async def show_account(query, user_id):
    user = data["users"].get(str(user_id), {})

    await query.edit_message_text(
        "👤 حساب کاربری\n\n"
        f"🆔 ID: {user.get('id', user_id)}\n"
        f"👤 نام: {esc(user.get('first_name', '-'))}\n"
        f"🔗 Username: @{esc(user.get('username') or '-')}\n"
        f"📦 سرویس‌ها: {len(user.get('service_ids', []))}\n"
        f"💳 موجودی: {money(user.get('balance', 0))} تومان\n"
        f"⭐ امتیاز: {user.get('points', 0)}\n"
        f"📅 عضویت: {user.get('created_at', '-')}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💳 کیف پول", callback_data="wallet"),
                InlineKeyboardButton("📜 تراکنش‌ها", callback_data="transactions"),
            ],
            [
                InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")
            ],
        ]),
    )


async def show_wallet(query, user_id):
    user = data["users"].get(str(user_id), {})

    await query.edit_message_text(
        "💳 کیف پول\n\n"
        f"💰 موجودی: {money(user.get('balance', 0))} تومان\n\n"
        "افزایش موجودی آنلاین در نسخه بعدی قابل اتصال به درگاه خواهد بود.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📜 تراکنش‌ها",
                    callback_data="transactions",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 حساب کاربری",
                    callback_data="account",
                )
            ],
        ]),
    )


async def show_transactions(query, user_id):
    txs = [
        t for t in data["transactions"].values()
        if t.get("user_id") == user_id
    ]
    txs = sorted(txs, key=lambda x: x.get("created_at", ""), reverse=True)[:10]

    if not txs:
        text = "📜 تراکنش‌ها\n\nتراکنشی ثبت نشده است."
    else:
        lines = ["📜 آخرین تراکنش‌ها\n"]
        for t in txs:
            sign = "+" if int(t.get("amount", 0)) >= 0 else ""
            lines.append(
                f"• {t.get('type', '-')}: "
                f"{sign}{money(t.get('amount', 0))} تومان\n"
                f"  {t.get('description', '-')}"
            )
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 حساب کاربری", callback_data="account")]
        ]),
    )


# =========================================================
# REFERRAL
# =========================================================

async def show_referral(query, context, user_id):
    user = data["users"].get(str(user_id), {})
    me = await context.bot.get_me()

    code = user.get("referral_code", "")
    link = f"https://t.me/{me.username}?start=ref_{code}"

    referrals = [
        r for r in data["referrals"].values()
        if r.get("referrer_id") == user_id
    ]

    await query.edit_message_text(
        "⭐ دعوت دوستان\n\n"
        f"🔗 لینک اختصاصی:\n{link}\n\n"
        f"👥 تعداد دعوت: {len(referrals)}\n"
        f"🎁 پاداش تنظیم‌شده: "
        f"{money(data['settings'].get('referral_reward', 0))} تومان",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")]
        ]),
    )


# =========================================================
# DISCOUNTS / SUPPORT
# =========================================================

async def show_discount(query, context):
    context.user_data["state"] = "enter_discount"

    await query.edit_message_text(
        "🎁 کد تخفیف\n\n"
        "کد تخفیف خودت را در یک پیام ارسال کن.\n\n"
        "برای لغو /cancel را بفرست.",
        reply_markup=back_home(),
    )


async def apply_discount(update, code):
    user = data["users"].get(str(update.effective_user.id))
    discount = data["discounts"].get(code.upper())

    if not discount or not discount.get("active"):
        await update.message.reply_text(
            "❌ کد تخفیف معتبر نیست.",
            reply_markup=user_keyboard(),
        )
        return

    now = datetime.now(timezone.utc)

    expires = parse_dt(discount.get("expires_at"))
    if expires and now > expires:
        discount["active"] = False
        await save_data()
        await update.message.reply_text(
            "❌ تاریخ این کد تخفیف گذشته است.",
            reply_markup=user_keyboard(),
        )
        return

    max_uses = int(discount.get("max_uses", 0))
    used = int(discount.get("used", 0))

    if max_uses > 0 and used >= max_uses:
        await update.message.reply_text(
            "❌ ظرفیت استفاده از این کد تمام شده است.",
            reply_markup=user_keyboard(),
        )
        return

    discount["used"] = used + 1
    user["points"] += int(discount.get("points", 0))

    await save_data()

    await update.message.reply_text(
        f"✅ کد {code.upper()} با موفقیت ثبت شد.\n\n"
        f"🎁 نوع: {discount.get('type', 'percent')}\n"
        f"💎 مقدار: {discount.get('value', 0)}",
        reply_markup=user_keyboard(),
    )


async def show_support(query, user_id):
    open_tickets = [
        t for t in data["tickets"].values()
        if t.get("user_id") == user_id and t.get("status") == "open"
    ]
    max_open = int(data["settings"].get("max_open_tickets_per_user", 3) or 3)

    if len(open_tickets) >= max_open:
        await query.edit_message_text(
            f"🎫 پشتیبانی\n\n"
            f"حداکثر {max_open} تیکت باز مجاز است.\n"
            "لطفاً یکی از تیکت‌های قبلی را تکمیل یا ببند.",
            reply_markup=back_home(),
        )
        return

    ticket_id = uid("ticket")

    data["tickets"][ticket_id] = {
        "id": ticket_id,
        "user_id": user_id,
        "subject": "",
        "messages": [],
        "status": "open",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data["users"][str(user_id)]["ticket_ids"].append(ticket_id)
    await save_data()

    await query.edit_message_text(
        "🎫 پشتیبانی\n\n"
        "یک تیکت جدید ساخته شد.\n"
        "پیام بعدی تو به عنوان متن تیکت ثبت می‌شود.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]
        ]),
    )

    # Put the user in support mode.
    # This is intentionally simple for a one-file bot.
    # A normal message sent afterward will be appended to the newest open ticket.
    return


# =========================================================
# ADMIN DASHBOARD
# =========================================================

def dashboard_text():
    users = list(data["users"].values())
    services = list(data["services"].values())
    orders = list(data["orders"].values())
    tickets = list(data["tickets"].values())

    active_users = sum(1 for u in users if not u.get("blocked"))
    active_owned = sum(1 for s in services if s.get("status") == "active")
    open_tickets = sum(1 for t in tickets if t.get("status") == "open")
    paid_orders = sum(1 for o in orders if o.get("status") == "paid")

    return (
        f"📊 داشبورد {BRAND}\n\n"
        f"👥 کل کاربران: {len(users)}\n"
        f"🟢 کاربران فعال: {active_users}\n"
        f"📦 سرویس‌های موجود: {len(services)}\n"
        f"✅ سرویس‌های فعال کاربران: {active_owned}\n"
        f"🛒 سفارش پرداخت‌شده: {paid_orders}\n"
        f"💰 درآمد ثبت‌شده: {money(data['stats'].get('total_revenue', 0))} تومان\n"
        f"🎫 تیکت باز: {open_tickets}\n"
        f"🎁 کد تخفیف: {len(data['discounts'])}\n"
        f"🧾 لاگ مدیریتی: {len(data.get('audit_logs', {}))}\n"
        f"🕒 نگهداری تیکت بسته: {data['settings'].get('ticket_retention_hours', 72)} ساعت\n\n"
        f"🕒 آخرین بروزرسانی:\n{data['meta'].get('updated_at', '-')}"
    )


# =========================================================
# ADMIN USER MANAGEMENT
# =========================================================

async def admin_users(query):
    users = sorted(
        data["users"].values(),
        key=lambda x: x.get("last_seen", ""),
        reverse=True,
    )[:25]

    rows = []

    for u in users:
        icon = "🚫" if u.get("blocked") else "👤"
        name = (u.get("first_name") or "User")[:24]
        rows.append([
            InlineKeyboardButton(
                f"{icon} {name} | {u.get('id')}",
                callback_data=f"auser:{u.get('id')}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔎 جستجو", callback_data="admin_user_search")
    ])
    rows.append([
        InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")
    ])

    await query.edit_message_text(
        "👥 مدیریت کاربران\n\n"
        f"تعداد کاربران: {len(data['users'])}\n"
        "آخرین کاربران:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_user_view(query, user_id):
    u = data["users"].get(str(user_id))

    if not u:
        await query.edit_message_text("❌ کاربر پیدا نشد.", reply_markup=back_admin())
        return

    rows = [
        [
            InlineKeyboardButton(
                "🚫 مسدود" if not u.get("blocked") else "✅ رفع مسدودی",
                callback_data=f"toggle_block:{user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 تغییر موجودی",
                callback_data=f"adjust_balance:{user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📨 پیام به کاربر",
                callback_data=f"message_user:{user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📦 سرویس‌های کاربر",
                callback_data=f"user_services:{user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 کاربران",
                callback_data="admin_users",
            )
        ],
    ]

    await query.edit_message_text(
        f"👤 کاربر\n\n"
        f"🆔 {u.get('id')}\n"
        f"👤 {esc(u.get('first_name', '-'))} {esc(u.get('last_name', ''))}\n"
        f"🔗 @{esc(u.get('username') or '-')}\n"
        f"💰 موجودی: {money(u.get('balance', 0))} تومان\n"
        f"📦 سرویس‌ها: {len(u.get('service_ids', []))}\n"
        f"⭐ امتیاز: {u.get('points', 0)}\n"
        f"🚦 وضعیت: {'مسدود' if u.get('blocked') else 'فعال'}\n"
        f"📅 عضویت: {u.get('created_at', '-')}\n"
        f"🕒 آخرین فعالیت: {u.get('last_seen', '-')}",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# =========================================================
# ADMIN SERVICES
# =========================================================

async def admin_services(query):
    services = list(data["services"].values())

    rows = [
        [
            InlineKeyboardButton(
                "➕ ساخت پلن",
                callback_data="admin_add_service",
            )
        ]
    ]

    for s in services[:30]:
        icon = "🟢" if s.get("active", True) else "🔴"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {s.get('name', 'پلن')}",
                callback_data=f"aservice:{s['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")
    ])

    await query.edit_message_text(
        f"📦 مدیریت سرویس‌ها\n\n"
        f"تعداد پلن‌ها: {len(services)}",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_service_view(query, service_id):
    s = data["services"].get(service_id)

    if not s:
        await query.edit_message_text(
            "❌ پلن پیدا نشد.",
            reply_markup=back_admin(),
        )
        return

    status = "🟢 فعال" if s.get("active", True) else "🔴 غیرفعال"

    await query.edit_message_text(
        f"📦 {esc(s.get('name', ''))}\n\n"
        f"💰 قیمت: {money(s.get('price', 0))} تومان\n"
        f"💾 حجم: {s.get('traffic_gb', 0)} GB\n"
        f"⏳ مدت: {s.get('duration_days', 0)} روز\n"
        f"🌍 سرور: {esc(s.get('server', '-'))}\n"
        f"🚦 وضعیت: {status}\n\n"
        f"{esc(s.get('description', ''))}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✏️ ویرایش",
                    callback_data=f"edit_service:{service_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 فعال/غیرفعال",
                    callback_data=f"toggle_service:{service_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑️ حذف",
                    callback_data=f"delete_service:{service_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 سرویس‌ها",
                    callback_data="admin_services",
                )
            ],
        ]),
    )


# =========================================================
# ADMIN ORDERS / TRANSACTIONS
# =========================================================

async def admin_orders(query):
    orders = sorted(
        data["orders"].values(),
        key=lambda x: x.get("created_at", ""),
        reverse=True,
    )[:30]

    if not orders:
        text = "🛒 سفارش‌ها\n\nهنوز سفارشی ثبت نشده است."
    else:
        lines = ["🛒 آخرین سفارش‌ها\n"]
        for o in orders:
            lines.append(
                f"• {o.get('id')}\n"
                f"  👤 {o.get('user_id')}\n"
                f"  💰 {money(o.get('amount', 0))} تومان\n"
                f"  🚦 {o.get('status')}\n"
            )
        text = "\n".join(lines)

    await query.edit_message_text(text, reply_markup=back_admin())


async def admin_transactions(query):
    txs = sorted(
        data["transactions"].values(),
        key=lambda x: x.get("created_at", ""),
        reverse=True,
    )[:30]

    if not txs:
        text = "💳 تراکنش‌ها\n\nتراکنشی ثبت نشده است."
    else:
        lines = ["💳 آخرین تراکنش‌ها\n"]
        for t in txs:
            lines.append(
                f"• {t.get('id')}\n"
                f"  👤 {t.get('user_id')}\n"
                f"  💰 {money(t.get('amount', 0))} تومان\n"
                f"  📝 {t.get('description', '-')}\n"
            )
        text = "\n".join(lines)

    await query.edit_message_text(text, reply_markup=back_admin())


# =========================================================
# ADMIN DISCOUNTS
# =========================================================

async def admin_discounts(query):
    rows = [
        [
            InlineKeyboardButton(
                "➕ ساخت کد",
                callback_data="admin_add_discount",
            )
        ]
    ]

    for code, d in list(data["discounts"].items())[:30]:
        state = "🟢" if d.get("active") else "🔴"
        rows.append([
            InlineKeyboardButton(
                f"{state} {code}",
                callback_data=f"adiscount:{code}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")
    ])

    await query.edit_message_text(
        f"🎁 تخفیف‌ها\n\n"
        f"تعداد کدها: {len(data['discounts'])}",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# =========================================================
# ADMIN REFERRAL / SUPPORT / REPORTS
# =========================================================

async def admin_referral(query):
    referrals = list(data["referrals"].values())

    await query.edit_message_text(
        "⭐ سیستم دعوت\n\n"
        f"👥 کل ارجاع‌ها: {len(referrals)}\n"
        f"🎁 پاداش هر دعوت: "
        f"{money(data['settings'].get('referral_reward', 0))} تومان\n\n"
        "مدیریت کامل این سیستم در همین فایل آماده است و "
        "می‌توان پاداش را از تنظیمات تغییر داد.",
        reply_markup=back_admin(),
    )


async def admin_support(query):
    tickets = sorted(
        data["tickets"].values(),
        key=lambda x: x.get("updated_at", ""),
        reverse=True,
    )[:30]

    rows = []

    for t in tickets:
        icon = "🟢" if t.get("status") == "open" else "⚫"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {t.get('id')} | {t.get('user_id')}",
                callback_data=f"ticket:{t['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")
    ])

    await query.edit_message_text(
        f"🎫 پشتیبانی\n\n"
        f"کل تیکت‌ها: {len(data['tickets'])}",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_reports(query):
    users = list(data["users"].values())
    paid = [
        o for o in data["orders"].values()
        if o.get("status") == "paid"
    ]

    today = datetime.now(timezone.utc).date().isoformat()
    today_orders = [
        o for o in paid
        if str(o.get("created_at", "")).startswith(today)
    ]

    today_revenue = sum(int(o.get("amount", 0)) for o in today_orders)

    await query.edit_message_text(
        "📈 گزارش‌ها\n\n"
        f"👥 کاربران: {len(users)}\n"
        f"🛒 فروش کل: {len(paid)}\n"
        f"💰 درآمد کل: {money(data['stats'].get('total_revenue', 0))} تومان\n"
        f"🛒 فروش امروز: {len(today_orders)}\n"
        f"💰 درآمد امروز: {money(today_revenue)} تومان\n"
        f"🎫 تیکت‌ها: {len(data['tickets'])}\n"
        f"⭐ دعوت‌ها: {len(data['referrals'])}",
        reply_markup=back_admin(),
    )


# =========================================================
# ADMIN BACKUP / RESTORE
# =========================================================

async def admin_backup(query):
    await query.edit_message_text(
        "💾 مدیریت بکاپ\n\n"
        "از این بخش می‌توانی بکاپ کامل JSON بگیری یا فایل قبلی را "
        "برای بازیابی ارسال کنی.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💾 دریافت بکاپ",
                    callback_data="backup_now",
                )
            ],
            [
                InlineKeyboardButton(
                    "📤 حالت بازیابی",
                    callback_data="restore_start",
                )
            ],
            [
                InlineKeyboardButton(
                    "📂 لیست بکاپ‌ها",
                    callback_data="backup_list",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 پنل مدیریت",
                    callback_data="admin_home",
                )
            ],
        ]),
    )


async def backup_now(query, context):
    path = await make_backup("manual")

    await context.bot.send_document(
        chat_id=query.from_user.id,
        document=InputFile(path),
        caption=(
            f"💾 بکاپ کامل {BRAND}\n\n"
            "این فایل شامل کاربران، سفارش‌ها، کیف پول، سرویس‌ها، "
            "تخفیف‌ها، تیکت‌ها و تنظیمات است."
        ),
    )

    await query.answer("بکاپ ساخته شد ✅")


async def backup_list(query):
    os.makedirs(BACKUP_DIR, exist_ok=True)

    names = sorted(
        os.listdir(BACKUP_DIR),
        reverse=True,
    )[:15]

    if not names:
        text = "📂 هنوز بکاپی وجود ندارد."
    else:
        text = "📂 بکاپ‌های موجود:\n\n" + "\n".join(
            f"• {n}" for n in names
        )

    await query.edit_message_text(text, reply_markup=back_admin())


# =========================================================
# ADMIN STAFF
# =========================================================

async def admin_staff(query):
    if not is_owner(query.from_user.id):
        await query.edit_message_text(
            "⛔ فقط Owner به مدیریت مدیران دسترسی دارد.",
            reply_markup=back_admin(),
        )
        return

    rows = [
        [
            InlineKeyboardButton(
                "➕ افزودن مدیر",
                callback_data="staff_add",
            )
        ]
    ]

    for uid_, record in data["admins"].items():
        role = record.get("role", "-")
        active = "🟢" if record.get("active") else "🔴"
        rows.append([
            InlineKeyboardButton(
                f"{active} {uid_} | {role}",
                callback_data=f"staff_view:{uid_}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")
    ])

    await query.edit_message_text(
        "🛡️ دسترسی مدیران\n\n"
        "Owner دسترسی کامل دارد.\n"
        "Roleها: owner / manager / finance / support",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# =========================================================
# SETTINGS
# =========================================================

async def admin_settings(query):
    s = data["settings"]

    await query.edit_message_text(
        "⚙️ تنظیمات\n\n"
        f"🏷️ برند: {s.get('brand', BRAND)}\n"
        f"📢 کانال: {s.get('channel_url', '-')}\n"
        f"🛑 حالت تعمیرات: {'روشن' if s.get('maintenance') else 'خاموش'}\n"
        f"🔔 اعلان انقضا: {'روشن' if s.get('auto_expiry_notice') else 'خاموش'}\n"
        f"🎁 پاداش دعوت: {money(s.get('referral_reward', 0))} تومان\n"
        f"💰 حداقل شارژ: {money(s.get('min_topup', 0))} تومان\n"
        f"🎫 نگهداری تیکت بسته: {s.get('ticket_retention_hours', 72)} ساعت\n"
        f"💾 بکاپ خودکار: {'روشن' if s.get('auto_backup_enabled', True) else 'خاموش'}\n"
        f"⭐ Loyalty: {'روشن' if s.get('loyalty_enabled', True) else 'خاموش'}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 تعمیرات روشن/خاموش",
                    callback_data="toggle_maintenance",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔔 اعلان انقضا",
                    callback_data="toggle_expiry",
                )
            ],
            [
                InlineKeyboardButton(
                    "💾 بکاپ خودکار روشن/خاموش",
                    callback_data="toggle_auto_backup",
                )
            ],
            [
                InlineKeyboardButton(
                    "⭐ Loyalty روشن/خاموش",
                    callback_data="toggle_loyalty",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎫 مدت نگهداری تیکت",
                    callback_data="set_ticket_retention",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎁 تغییر پاداش دعوت",
                    callback_data="set_referral_reward",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 پنل مدیریت",
                    callback_data="admin_home",
                )
            ],
        ]),
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = await ensure_user(query.from_user)
    if user.get("blocked") and not is_owner(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی شما مسدود است.")
        return

    data_key = query.data or ""

    # User
    if data_key == "home":
        await query.edit_message_text(
            f"🏠 منوی اصلی {BRAND}",
            reply_markup=user_keyboard(),
        )
        return

    if data_key == "shop":
        await show_shop(query)
        return

    if data_key.startswith("view_service:"):
        await view_service(query, data_key.split(":", 1)[1])
        return

    if data_key.startswith("buy_wallet:"):
        await buy_with_wallet(
            query,
            query.from_user.id,
            data_key.split(":", 1)[1],
        )
        return

    if data_key == "my_services":
        await show_my_services(query, query.from_user.id)
        return

    if data_key.startswith("owned_service:"):
        await show_owned_service(
            query,
            data_key.split(":", 1)[1],
        )
        return

    if data_key == "account":
        await show_account(query, query.from_user.id)
        return

    if data_key == "wallet":
        await show_wallet(query, query.from_user.id)
        return

    if data_key == "transactions":
        await show_transactions(query, query.from_user.id)
        return

    if data_key == "discount":
        await show_discount(query, context)
        return

    if data_key == "referral":
        await show_referral(
            query,
            context,
            query.from_user.id,
        )
        return

    if data_key == "support":
        await show_support(query, query.from_user.id)
        context.user_data["state"] = "support_message"
        return

    if data_key == "channel":
        await query.edit_message_text(
            "📢 کانال رسمی NovaLinkVPN",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🌐 ورود به کانال",
                        url=data["settings"].get("channel_url", CHANNEL_URL),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 منوی اصلی",
                        callback_data="home",
                    )
                ],
            ]),
        )
        return

    # Admin guard
    role = get_admin_role(query.from_user.id)
    if data_key.startswith("admin_") or data_key.startswith((
        "backup", "restore", "auser:", "toggle_block:", "adjust_balance:",
        "message_user:", "user_services:", "aservice:", "edit_service:",
        "toggle_service:", "delete_service:", "adiscount:", "ticket:",
        "staff_", "set_", "toggle_maintenance", "toggle_expiry",
        "renew:"
    )):
        if not role:
            await query.edit_message_text("⛔ دسترسی غیرمجاز.")
            return

    # Admin main
    if data_key == "admin_home":
        await query.edit_message_text(
            f"👑 پنل مدیریت {BRAND}\n\nسطح دسترسی: {role}",
            reply_markup=admin_keyboard(role),
        )
        return

    if data_key == "admin_dashboard":
        await query.edit_message_text(
            dashboard_text(),
            reply_markup=back_admin(),
        )
        return

    if data_key == "admin_users":
        if not can_manage_users(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_users(query)
        return

    if data_key == "admin_user_search":
        context.user_data["state"] = "admin_user_search"
        await query.edit_message_text(
            "🔎 شناسه عددی یا username کاربر را بفرست.\n/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("auser:"):
        if not can_manage_users(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_user_view(query, data_key.split(":", 1)[1])
        return

    if data_key.startswith("toggle_block:"):
        if not can_manage_users(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        uid_ = data_key.split(":", 1)[1]
        u = data["users"].get(str(uid_))
        if u:
            u["blocked"] = not u.get("blocked", False)
            await save_data()
        await admin_user_view(query, uid_)
        return

    if data_key == "admin_services":
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_services(query)
        return

    if data_key.startswith("aservice:"):
        await admin_service_view(query, data_key.split(":", 1)[1])
        return

    if data_key == "admin_add_service":
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        context.user_data["state"] = "admin_add_service"
        context.user_data["service_draft"] = {}
        await query.edit_message_text(
            "➕ ساخت پلن\n\n"
            "به ترتیب این اطلاعات را در یک پیام بفرست:\n\n"
            "نام | قیمت | حجم GB | مدت روز | سرور | توضیح\n\n"
            "مثال:\n"
            "VIP 1M | 150000 | 100 | 30 | Germany | سرویس VIP\n\n"
            "/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key == "admin_orders":
        if not can_manage_finance(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_orders(query)
        return

    if data_key == "admin_transactions":
        if not can_manage_finance(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_transactions(query)
        return

    if data_key == "admin_discounts":
        if not can_manage_finance(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_discounts(query)
        return

    if data_key == "admin_add_discount":
        if not can_manage_finance(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        context.user_data["state"] = "admin_add_discount"
        await query.edit_message_text(
            "➕ ساخت کد تخفیف\n\n"
            "فرمت:\n"
            "CODE | نوع(percent/fixed) | مقدار | حداکثر استفاده | مدت روز\n\n"
            "مثال:\n"
            "WELCOME20 | percent | 20 | 100 | 30\n\n"
            "/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("adiscount:"):
        code = data_key.split(":", 1)[1]
        d = data["discounts"].get(code)
        if not d:
            await query.edit_message_text("❌ کد پیدا نشد.", reply_markup=back_admin())
            return

        await query.edit_message_text(
            f"🎁 {code}\n\n"
            f"نوع: {d.get('type')}\n"
            f"مقدار: {d.get('value')}\n"
            f"استفاده: {d.get('used', 0)} / {d.get('max_uses', 0) or '∞'}\n"
            f"فعال: {'بله' if d.get('active') else 'خیر'}\n"
            f"انقضا: {d.get('expires_at', '-')}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 فعال/غیرفعال",
                        callback_data=f"toggle_discount:{code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 تخفیف‌ها",
                        callback_data="admin_discounts",
                    )
                ],
            ]),
        )
        return

    if data_key.startswith("toggle_discount:"):
        code = data_key.split(":", 1)[1]
        d = data["discounts"].get(code)
        if d:
            d["active"] = not d.get("active", False)
            await save_data()
        await query.edit_message_text(
            "✅ وضعیت کد تغییر کرد.",
            reply_markup=back_admin(),
        )
        return

    if data_key == "admin_referral":
        await admin_referral(query)
        return

    if data_key == "admin_support":
        if not can_manage_support(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_support(query)
        return

    if data_key.startswith("ticket:"):
        if not can_manage_support(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        tid = data_key.split(":", 1)[1]
        ticket = data["tickets"].get(tid)
        if not ticket:
            await query.edit_message_text("❌ تیکت پیدا نشد.", reply_markup=back_admin())
            return

        messages = ticket.get("messages", [])
        last = messages[-5:]
        body = "\n\n".join(
            f"{m.get('from')}: {m.get('text', '')}"
            for m in last
        ) or "پیامی ثبت نشده."

        await query.edit_message_text(
            f"🎫 تیکت {tid}\n\n"
            f"👤 User: {ticket.get('user_id')}\n"
            f"🚦 وضعیت: {ticket.get('status')}\n\n"
            f"{body}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ بستن تیکت",
                        callback_data=f"close_ticket:{tid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📨 پاسخ",
                        callback_data=f"reply_ticket:{tid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 پشتیبانی",
                        callback_data="admin_support",
                    )
                ],
            ]),
        )
        return

    if data_key.startswith("close_ticket:"):
        if not can_manage_support(query.from_user.id):
            await query.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        tid = data_key.split(":", 1)[1]
        ticket = data["tickets"].get(tid)
        if ticket:
            ticket["status"] = "closed"
            ticket["closed_at"] = now_iso()
            ticket["updated_at"] = ticket["closed_at"]
            await save_data()
            await audit_action(
                query.from_user.id,
                "close_ticket",
                tid,
                f"user={ticket.get('user_id')}",
            )
            try:
                await context.bot.send_message(
                    chat_id=int(ticket.get("user_id")),
                    text=(
                        f"✅ تیکت {tid} بسته شد.\n\n"
                        "این تیکت تا ۷۲ ساعت نگهداری می‌شود و سپس خودکار حذف خواهد شد."
                    ),
                )
            except Exception:
                pass
        await query.edit_message_text(
            "✅ تیکت بسته شد. حذف خودکار پس از ۷۲ ساعت.",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("reply_ticket:"):
        context.user_data["state"] = f"reply_ticket:{data_key.split(':', 1)[1]}"
        await query.edit_message_text(
            "📨 پاسخ تیکت را در پیام بعدی بفرست.\n/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key == "admin_broadcast":
        if not can_broadcast(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        context.user_data["state"] = "admin_broadcast"
        await query.edit_message_text(
            "📢 ارسال همگانی\n\n"
            "متن پیام را بفرست.\n"
            "پیام برای کاربران غیرمسدود ارسال می‌شود.\n\n"
            "/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key == "admin_reports":
        await admin_reports(query)
        return

    if data_key == "admin_backup":
        await admin_backup(query)
        return

    if data_key == "backup_now":
        if not is_owner(query.from_user.id):
            await query.answer("فقط Owner می‌تواند بکاپ بگیرد.", show_alert=True)
            return
        await backup_now(query, context)
        return

    if data_key == "backup_list":
        await backup_list(query)
        return

    if data_key == "restore_start":
        if not is_owner(query.from_user.id):
            await query.answer("فقط Owner می‌تواند Restore کند.", show_alert=True)
            return

        context.user_data["state"] = "restore_file"
        await query.edit_message_text(
            "📤 بازیابی بکاپ\n\n"
            "فایل JSON بکاپ را همینجا ارسال کن.\n"
            "این کار داده فعلی را با داده بکاپ جایگزین می‌کند.\n\n"
            "قبل از Restore بهتر است یک Backup جدید بگیری.\n"
            "/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key == "admin_staff":
        await admin_staff(query)
        return

    if data_key == "staff_add":
        if not is_owner(query.from_user.id):
            await query.edit_message_text("⛔ فقط Owner.", reply_markup=back_admin())
            return
        context.user_data["state"] = "staff_add"
        await query.edit_message_text(
            "➕ افزودن مدیر\n\n"
            "فرمت:\n"
            "TELEGRAM_ID | ROLE\n\n"
            "Roleها:\n"
            "manager\n"
            "finance\n"
            "support\n\n"
            "مثال:\n"
            "123456789 | support",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("staff_view:"):
        if not is_owner(query.from_user.id):
            await query.edit_message_text("⛔ فقط Owner.", reply_markup=back_admin())
            return

        uid_ = data_key.split(":", 1)[1]
        record = data["admins"].get(uid_)

        if not record:
            await query.edit_message_text("❌ مدیر پیدا نشد.", reply_markup=back_admin())
            return

        await query.edit_message_text(
            f"🛡️ مدیر {uid_}\n\n"
            f"Role: {record.get('role')}\n"
            f"Active: {record.get('active')}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 فعال/غیرفعال",
                        callback_data=f"toggle_staff:{uid_}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗑️ حذف",
                        callback_data=f"delete_staff:{uid_}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 مدیران",
                        callback_data="admin_staff",
                    )
                ],
            ]),
        )
        return

    if data_key.startswith("toggle_staff:"):
        uid_ = data_key.split(":", 1)[1]
        if uid_ == str(ADMIN_ID):
            await query.answer("Owner قابل غیرفعال کردن نیست.", show_alert=True)
            return
        record = data["admins"].get(uid_)
        if record:
            record["active"] = not record.get("active", True)
            await save_data()
        await admin_staff(query)
        return

    if data_key.startswith("delete_staff:"):
        uid_ = data_key.split(":", 1)[1]
        if uid_ == str(ADMIN_ID):
            await query.answer("Owner قابل حذف نیست.", show_alert=True)
            return
        data["admins"].pop(uid_, None)
        await save_data()
        await admin_staff(query)
        return

    if data_key == "admin_settings":
        await admin_settings(query)
        return

    if data_key == "toggle_maintenance":
        data["settings"]["maintenance"] = not data["settings"].get("maintenance", False)
        await save_data()
        await admin_settings(query)
        return

    if data_key == "toggle_expiry":
        data["settings"]["auto_expiry_notice"] = not data["settings"].get("auto_expiry_notice", True)
        await save_data()
        await admin_settings(query)
        return

    if data_key == "toggle_auto_backup":
        if not is_owner(query.from_user.id):
            await query.answer("فقط Owner.", show_alert=True)
            return
        data["settings"]["auto_backup_enabled"] = not data["settings"].get("auto_backup_enabled", True)
        await save_data()
        await audit_action(query.from_user.id, "toggle_auto_backup", "", str(data["settings"]["auto_backup_enabled"]))
        await admin_settings(query)
        return

    if data_key == "toggle_loyalty":
        if not is_owner(query.from_user.id):
            await query.answer("فقط Owner.", show_alert=True)
            return
        data["settings"]["loyalty_enabled"] = not data["settings"].get("loyalty_enabled", True)
        await save_data()
        await audit_action(query.from_user.id, "toggle_loyalty", "", str(data["settings"]["loyalty_enabled"]))
        await admin_settings(query)
        return

    if data_key == "set_ticket_retention":
        if not is_owner(query.from_user.id):
            await query.answer("فقط Owner.", show_alert=True)
            return
        context.user_data["state"] = "set_ticket_retention"
        await query.edit_message_text(
            "🎫 مدت نگهداری تیکت بسته را به ساعت بفرست.\n"
            "پیشنهاد: 72\n\n"
            "/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key == "set_referral_reward":
        context.user_data["state"] = "set_referral_reward"
        await query.edit_message_text(
            "🎁 مقدار پاداش دعوت را به تومان بفرست.\n"
            "مثال: 50000",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("adjust_balance:"):
        if not can_manage_finance(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        uid_ = data_key.split(":", 1)[1]
        context.user_data["state"] = f"adjust_balance:{uid_}"
        await query.edit_message_text(
            "💰 مقدار تغییر موجودی را بفرست.\n"
            "مثال +50000 یا -20000",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("message_user:"):
        if not can_manage_users(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        uid_ = data_key.split(":", 1)[1]
        context.user_data["state"] = f"message_user:{uid_}"
        await query.edit_message_text(
            "📨 پیام کاربر را بفرست.",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("user_services:"):
        uid_ = data_key.split(":", 1)[1]
        u = data["users"].get(uid_)

        if not u:
            await query.edit_message_text("❌ کاربر پیدا نشد.", reply_markup=back_admin())
            return

        lines = [f"📦 سرویس‌های کاربر {uid_}\n"]

        for sid in u.get("service_ids", []):
            s = data["services"].get(sid)
            if s:
                lines.append(
                    f"• {s.get('plan_name', '-')}\n"
                    f"  {s.get('status', '-')} | {s.get('expires_at', '-')}"
                )

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("toggle_service:"):
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return

        sid = data_key.split(":", 1)[1]
        s = data["services"].get(sid)

        if s:
            s["active"] = not s.get("active", True)
            await save_data()

        await admin_service_view(query, sid)
        return

    if data_key.startswith("delete_service:"):
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return

        sid = data_key.split(":", 1)[1]
        data["services"].pop(sid, None)
        await save_data()

        await query.edit_message_text(
            "✅ پلن حذف شد.",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("edit_service:"):
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return

        sid = data_key.split(":", 1)[1]
        context.user_data["state"] = f"edit_service:{sid}"
        await query.edit_message_text(
            "✏️ ویرایش پلن\n\n"
            "فرمت:\n"
            "نام | قیمت | حجم GB | مدت روز | سرور | توضیح",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("renew:"):
        await query.answer("سیستم تمدید در نسخه بعدی تکمیل می‌شود.", show_alert=True)
        return


# =========================================================
# MESSAGE INPUT HANDLER
# =========================================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = await ensure_user(update.effective_user)

    if user.get("blocked") and not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی شما مسدود است.")
        return

    text = (update.message.text or "").strip()

    if text == "/cancel":
        context.user_data.clear()
        await update.message.reply_text(
            "✅ عملیات لغو شد.",
            reply_markup=user_keyboard() if not get_admin_role(update.effective_user.id)
            else admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    state = context.user_data.get("state")

    # Restore JSON file
    if state == "restore_file":
        await update.message.reply_text(
            "📤 برای Restore یک فایل JSON ارسال کن، نه متن معمولی."
        )
        return

    # Discount
    if state == "enter_discount":
        context.user_data.pop("state", None)
        await apply_discount(update, text)
        return

    # Admin service creation
    if state == "admin_add_service":
        if not can_manage_services(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        parts = [x.strip() for x in text.split("|")]

        if len(parts) < 6:
            await update.message.reply_text(
                "❌ فرمت اشتباه است.\n"
                "نام | قیمت | حجم GB | مدت روز | سرور | توضیح"
            )
            return

        try:
            name = parts[0]
            price = int(parts[1])
            traffic = int(parts[2])
            days = int(parts[3])
        except ValueError:
            await update.message.reply_text(
                "❌ قیمت، حجم و مدت باید عدد باشند."
            )
            return

        sid = uid("plan")

        data["services"][sid] = {
            "id": sid,
            "name": name,
            "price": price,
            "traffic_gb": traffic,
            "duration_days": days,
            "server": parts[4],
            "description": parts[5],
            "active": True,
            "created_at": now_iso(),
        }

        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            f"✅ پلن ساخته شد.\n\n"
            f"📦 {name}\n"
            f"💰 {money(price)} تومان\n"
            f"💾 {traffic} GB\n"
            f"⏳ {days} روز",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # Admin service edit
    if state and state.startswith("edit_service:"):
        if not can_manage_services(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        sid = state.split(":", 1)[1]
        s = data["services"].get(sid)

        parts = [x.strip() for x in text.split("|")]

        if not s or len(parts) < 6:
            await update.message.reply_text(
                "❌ فرمت اشتباه است.\n"
                "نام | قیمت | حجم GB | مدت روز | سرور | توضیح"
            )
            return

        try:
            s["name"] = parts[0]
            s["price"] = int(parts[1])
            s["traffic_gb"] = int(parts[2])
            s["duration_days"] = int(parts[3])
            s["server"] = parts[4]
            s["description"] = parts[5]
        except ValueError:
            await update.message.reply_text("❌ قیمت، حجم و مدت باید عدد باشند.")
            return

        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            "✅ پلن ویرایش شد.",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # Admin discount
    if state == "admin_add_discount":
        if not can_manage_finance(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        parts = [x.strip() for x in text.split("|")]

        if len(parts) < 5:
            await update.message.reply_text(
                "❌ فرمت:\n"
                "CODE | percent/fixed | value | max_uses | days"
            )
            return

        code = parts[0].upper()

        try:
            value = int(parts[2])
            max_uses = int(parts[3])
            days = int(parts[4])
        except ValueError:
            await update.message.reply_text("❌ مقدارها باید عدد باشند.")
            return

        expires = datetime.now(timezone.utc) + timedelta(days=days)

        data["discounts"][code] = {
            "code": code,
            "type": parts[1],
            "value": value,
            "max_uses": max_uses,
            "used": 0,
            "points": 0,
            "active": True,
            "created_at": now_iso(),
            "expires_at": expires.isoformat(),
        }

        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            f"✅ کد {code} ساخته شد.",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # Admin user search
    if state == "admin_user_search":
        if not can_manage_users(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        query = text.lstrip("@").lower()

        found = []

        for u in data["users"].values():
            if (
                str(u.get("id")) == query
                or str(u.get("username", "")).lower() == query
            ):
                found.append(u)

        context.user_data.clear()

        if not found:
            await update.message.reply_text(
                "❌ کاربری پیدا نشد.",
                reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
            )
            return

        u = found[0]
        await update.message.reply_text(
            f"👤 کاربر پیدا شد:\n"
            f"ID: {u.get('id')}\n"
            f"Name: {u.get('first_name')}\n"
            f"Username: @{u.get('username') or '-'}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👤 مدیریت کاربر",
                        callback_data=f"auser:{u.get('id')}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 کاربران",
                        callback_data="admin_users",
                    )
                ],
            ]),
        )
        return

    # Adjust balance
    if state and state.startswith("adjust_balance:"):
        if not can_manage_finance(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        uid_ = state.split(":", 1)[1]
        target = data["users"].get(uid_)

        try:
            amount = int(text)
        except ValueError:
            await update.message.reply_text("❌ عدد معتبر وارد کن.")
            return

        if not target:
            context.user_data.clear()
            await update.message.reply_text("❌ کاربر پیدا نشد.")
            return

        target["balance"] = target.get("balance", 0) + amount

        txid = uid("tx")
        data["transactions"][txid] = {
            "id": txid,
            "user_id": int(uid_),
            "type": "admin_balance_adjustment",
            "amount": amount,
            "description": "تغییر موجودی توسط مدیریت",
            "created_at": now_iso(),
            "reference": "",
        }

        target["transaction_ids"].append(txid)

        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            f"✅ موجودی تغییر کرد.\n"
            f"موجودی جدید: {money(target['balance'])} تومان",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # Message user
    if state and state.startswith("message_user:"):
        if not can_manage_users(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        uid_ = state.split(":", 1)[1]
        context.user_data.clear()

        try:
            await context.bot.send_message(
                chat_id=int(uid_),
                text=f"📨 پیام از {BRAND}:\n\n{text}",
            )
            await update.message.reply_text(
                "✅ پیام ارسال شد.",
                reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ ارسال نشد:\n{e}",
                reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
            )
        return

    # Reply ticket
    if state and state.startswith("reply_ticket:"):
        if not can_manage_support(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        tid = state.split(":", 1)[1]
        ticket = data["tickets"].get(tid)

        if not ticket:
            context.user_data.clear()
            await update.message.reply_text("❌ تیکت پیدا نشد.")
            return

        ticket["messages"].append({
            "from": f"admin:{update.effective_user.id}",
            "text": text,
            "created_at": now_iso(),
        })
        ticket["updated_at"] = now_iso()

        context.user_data.clear()
        await save_data()

        try:
            await context.bot.send_message(
                chat_id=int(ticket["user_id"]),
                text=f"📨 پاسخ پشتیبانی:\n\n{text}",
            )
        except Exception:
            pass

        await update.message.reply_text(
            "✅ پاسخ ارسال شد.",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # Broadcast
    if state == "admin_broadcast":
        if not can_broadcast(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        context.user_data.clear()

        broadcast_id = uid("broadcast")
        record = {
            "id": broadcast_id,
            "admin_id": update.effective_user.id,
            "text": text,
            "created_at": now_iso(),
            "sent": 0,
            "failed": 0,
        }

        data["broadcasts"][broadcast_id] = record
        await save_data()

        sent = 0
        failed = 0

        await update.message.reply_text(
            "📢 ارسال شروع شد...\n"
            "ممکن است کمی زمان ببرد."
        )

        for u in data["users"].values():
            if u.get("blocked"):
                continue

            try:
                await context.bot.send_message(
                    chat_id=int(u["id"]),
                    text=text,
                )
                sent += 1
            except Exception:
                failed += 1

            await asyncio.sleep(0.05)

        record["sent"] = sent
        record["failed"] = failed
        await save_data()

        await update.message.reply_text(
            f"✅ ارسال تمام شد.\n\n"
            f"📨 موفق: {sent}\n"
            f"❌ ناموفق: {failed}",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # Staff add
    if state == "staff_add":
        if not is_owner(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ فقط Owner.")
            return

        parts = [x.strip() for x in text.split("|")]

        if len(parts) < 2:
            await update.message.reply_text(
                "❌ فرمت: TELEGRAM_ID | ROLE"
            )
            return

        uid_ = parts[0]
        role = parts[1].lower()

        if role not in {"manager", "finance", "support"}:
            await update.message.reply_text(
                "❌ Role باید manager / finance / support باشد."
            )
            return

        data["admins"][uid_] = {
            "role": role,
            "active": True,
            "created_at": now_iso(),
        }

        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            "✅ مدیر اضافه شد.",
            reply_markup=admin_keyboard("owner"),
        )
        return

    # Ticket retention
    if state == "set_ticket_retention":
        if not is_owner(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ فقط Owner.")
            return
        try:
            hours = int(text)
            if hours < 1 or hours > 8760:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ عدد ساعت باید بین 1 تا 8760 باشد.")
            return

        data["settings"]["ticket_retention_hours"] = hours
        context.user_data.clear()
        await save_data()
        await audit_action(update.effective_user.id, "set_ticket_retention", "", str(hours))
        await update.message.reply_text(
            f"✅ مدت نگهداری تیکت‌های بسته روی {hours} ساعت تنظیم شد.",
            reply_markup=admin_keyboard("owner"),
        )
        return

    # Referral reward
    if state == "set_referral_reward":
        if not is_owner(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ فقط Owner.")
            return

        try:
            value = int(text)
        except ValueError:
            await update.message.reply_text("❌ عدد معتبر وارد کن.")
            return

        data["settings"]["referral_reward"] = max(0, value)
        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            "✅ پاداش دعوت تغییر کرد.",
            reply_markup=admin_keyboard("owner"),
        )
        return

    # Support message
    if state == "support_message":
        open_tickets = [
            t for t in data["tickets"].values()
            if t.get("user_id") == update.effective_user.id
            and t.get("status") == "open"
        ]

        context.user_data.clear()

        if not open_tickets:
            await update.message.reply_text(
                "❌ تیکت بازی پیدا نشد.",
                reply_markup=user_keyboard(),
            )
            return

        ticket = sorted(
            open_tickets,
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )[0]

        ticket["messages"].append({
            "from": f"user:{update.effective_user.id}",
            "text": text,
            "created_at": now_iso(),
        })
        ticket["updated_at"] = now_iso()

        await save_data()

        # Notify owner
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🎫 تیکت جدید / پاسخ جدید\n\n"
                        f"Ticket: {ticket['id']}\n"
                        f"User: {update.effective_user.id}\n\n"
                        f"{text}"
                    ),
                )
            except Exception:
                pass

        await update.message.reply_text(
            "✅ پیام به پشتیبانی ارسال شد.",
            reply_markup=user_keyboard(),
        )
        return

    # Default
    await update.message.reply_text(
        "از منوی زیر استفاده کن:",
        reply_markup=user_keyboard(),
    )


# =========================================================
# DOCUMENT HANDLER - RESTORE
# =========================================================

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.document:
        return

    user = await ensure_user(update.effective_user)

    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط Owner می‌تواند Restore کند.")
        return

    if context.user_data.get("state") != "restore_file":
        await update.message.reply_text(
            "📄 فایل دریافت شد، اما حالت Restore فعال نیست."
        )
        return

    doc = update.message.document

    if not doc.file_name.lower().endswith(".json"):
        await update.message.reply_text(
            "❌ فقط فایل .json قبول می‌شود."
        )
        return

    tg_file = await context.bot.get_file(doc.file_id)
    content = await tg_file.download_as_bytearray()

    try:
        restored = restore_from_bytes(bytes(content))
    except Exception as e:
        context.user_data.clear()
        await update.message.reply_text(
            f"❌ بکاپ معتبر نیست:\n{e}",
            reply_markup=admin_keyboard("owner"),
        )
        return

    # Safety backup before replacing
    try:
        await make_backup("before_restore")
    except Exception:
        logger.exception("Could not create pre-restore backup.")

    global data
    data = restored
    normalize_data()
    await save_data()

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Restore با موفقیت انجام شد.\n\n"
        "داده‌های JSON جایگزین شدند.",
        reply_markup=admin_keyboard("owner"),
    )


# =========================================================
# TICKET RETENTION / CLEANUP
# =========================================================

async def ticket_cleanup_worker():
    while True:
        try:
            retention_hours = int(
                data["settings"].get("ticket_retention_hours", 72) or 72
            )
            cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
            deleted = []

            for tid, ticket in list(data["tickets"].items()):
                if ticket.get("status") != "closed":
                    continue

                closed_at = parse_dt(
                    ticket.get("closed_at") or ticket.get("updated_at")
                )
                if not closed_at or closed_at > cutoff:
                    continue

                user = data["users"].get(str(ticket.get("user_id")))
                if user:
                    user["ticket_ids"] = [
                        x for x in user.get("ticket_ids", []) if x != tid
                    ]

                data["tickets"].pop(tid, None)
                deleted.append(tid)

            if deleted:
                await save_data()
                logger.info("Auto-deleted %d closed tickets.", len(deleted))

            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Ticket cleanup worker error.")
            await asyncio.sleep(60)


# =========================================================
# EXPIRY NOTIFICATIONS
# =========================================================

async def expiry_worker(application: Application):
    while True:
        try:
            if data["settings"].get("auto_expiry_notice", True):
                await send_expiry_notifications(application)

            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Expiry worker error.")
            await asyncio.sleep(60)


async def send_expiry_notifications(application):
    now = datetime.now(timezone.utc)
    notice_days = data["settings"].get(
        "expiry_notice_days",
        [7, 3, 1],
    )

    changed = False

    for service in data["services"].values():
        if service.get("status") != "active":
            continue

        expires = parse_dt(service.get("expires_at"))
        if not expires:
            continue

        days_left = (expires - now).total_seconds() / 86400

        for target_day in notice_days:
            if 0 <= days_left <= target_day + 0.1:
                user_id = service.get("user_id")
                user = data["users"].get(str(user_id))

                if not user:
                    continue

                notice_key = f"{service['id']}:{target_day}"

                if user.setdefault("expiry_notices", {}).get(notice_key):
                    continue

                try:
                    await application.bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            "⏰ یادآوری انقضای سرویس\n\n"
                            f"📦 {service.get('plan_name', 'سرویس')}\n"
                            f"⏳ حدود {target_day} روز تا انقضا باقی مانده است.\n"
                            f"📅 {expires.strftime('%Y-%m-%d %H:%M')}"
                        ),
                    )

                    user["expiry_notices"][notice_key] = now_iso()
                    changed = True

                except Exception:
                    pass

    if changed:
        await save_data()


# =========================================================
# AUTO BACKUP
# =========================================================

async def auto_backup_worker():
    while True:
        try:
            await asyncio.sleep(AUTO_BACKUP_HOURS * 3600)
            if data["settings"].get("auto_backup_enabled", True):
                await make_backup("auto")
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Auto backup error.")


# =========================================================
# HEALTH / HTTP
# =========================================================

async def health(request):
    return web.Response(
        text="OK",
        status=200,
        content_type="text/plain",
    )


async def root(request):
    return web.Response(
        text=f"{BRAND} Bot is running.",
        status=200,
        content_type="text/plain",
    )


async def telegram_webhook(request):
    try:
        payload = await request.json()
        update = Update.de_json(
            payload,
            application.bot,
        )
        await application.process_update(update)

        return web.Response(text="OK", status=200)

    except Exception:
        logger.exception("Webhook processing error.")
        return web.Response(text="ERROR", status=500)


# =========================================================
# MAIN
# =========================================================

async def main():
    global application

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if ADMIN_ID == 0:
        logger.warning(
            "ADMIN_ID is not configured. Admin panel will be unavailable."
        )

    load_data()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("myid", myid_command))

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            document_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    await application.initialize()
    await application.start()

    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True,
    )

    logger.info("Webhook set: %s", WEBHOOK_URL)

    http_app = web.Application()

    http_app.router.add_get("/", root)
    http_app.router.add_get("/health", health)
    http_app.router.add_post(WEBHOOK_PATH, telegram_webhook)

    runner = web.AppRunner(http_app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logger.info("HTTP server started on 0.0.0.0:%s", PORT)
    logger.info("Health: %s/health", RENDER_URL)

    expiry_task = asyncio.create_task(
        expiry_worker(application)
    )

    backup_task = asyncio.create_task(
        auto_backup_worker()
    )

    ticket_cleanup_task = asyncio.create_task(
        ticket_cleanup_worker()
    )

    try:
        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

    finally:
        expiry_task.cancel()
        backup_task.cancel()
        ticket_cleanup_task.cancel()

        await runner.cleanup()

        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
