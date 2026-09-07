import os
import json
import asyncio
import logging
import re
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
PUBLIC_OWNER_USERNAME = "ELFERN_AMI"
PUBLIC_OWNER_URL = f"https://t.me/{PUBLIC_OWNER_USERNAME}"

DATA_FILE = "data.json"
BACKUP_DIR = "backups"
AUTO_BACKUP_HOURS = 6
MAX_BACKUPS = 20

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("novalinkvpn")


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
            "version": 3,
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
            "closed_ticket_retention_hours": 72,
        },
        "users": {},
        "services": {},
        "orders": {},
        "transactions": {},
        "discounts": {},
        "referrals": {},
        "tickets": {},
        "broadcasts": {},
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
application = None
data_lock = asyncio.Lock()
purchase_lock = asyncio.Lock()
test_lock = asyncio.Lock()


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
    normalize_inventory()


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
    """Validate and normalize a NovaLinkVPN JSON backup.

    Backups from older bot versions are accepted as long as their core
    structures are valid; newer fields are recreated by normalize_data().
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("Backup data is not valid.")

    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Backup file is too large (maximum 10 MB).")

    try:
        restored = json.loads(bytes(raw).decode("utf-8-sig"))
    except UnicodeDecodeError as e:
        raise ValueError("Backup must be a UTF-8 JSON file.") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON at line {e.lineno}, column {e.colno}.") from e

    if not isinstance(restored, dict):
        raise ValueError("Backup root must be a JSON object.")

    # Core structures required by the bot. Missing optional/newer fields
    # are intentionally recreated instead of rejecting older backups.
    required_dicts = [
        "settings", "users", "services", "orders",
        "transactions", "discounts", "referrals", "tickets",
        "broadcasts", "admins",
    ]

    for key in required_dicts:
        if key not in restored:
            raise ValueError(f"Missing required section: {key}")
        if not isinstance(restored[key], dict):
            raise ValueError(f"Invalid structure for section: {key}")

    if not isinstance(restored.get("meta", {}), dict):
        raise ValueError("Invalid meta section.")

    restored.setdefault("meta", {})
    restored.setdefault("schema_version", 1)

    # Newer systems can safely be restored from older backups.
    restored.setdefault("test_configs", {})
    restored.setdefault("test_claims", {})
    restored.setdefault("audit_log", [])
    restored.setdefault("notifications", {})

    if not isinstance(restored["test_configs"], dict):
        restored["test_configs"] = {}
    if not isinstance(restored["test_claims"], dict):
        restored["test_claims"] = {}

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
    unread = unread_notifications(getattr(user_keyboard, "current_user_id", 0)) if getattr(user_keyboard, "current_user_id", 0) else 0
    label = f"🔔 اعلان‌ها ({unread})" if unread else "🔔 اعلان‌ها"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop"), InlineKeyboardButton("🧪 تست رایگان", callback_data="test_menu")],
        [InlineKeyboardButton("📦 سرویس‌های من", callback_data="my_services"), InlineKeyboardButton("👤 حساب کاربری", callback_data="account")],
        [InlineKeyboardButton("💳 کیف پول", callback_data="wallet"), InlineKeyboardButton("🧾 سفارش‌ها", callback_data="orders")],
        [InlineKeyboardButton(label, callback_data="notifications"), InlineKeyboardButton("🎁 کد تخفیف", callback_data="discount")],
        [InlineKeyboardButton("⭐ دعوت دوستان", callback_data="referral"), InlineKeyboardButton("🎫 پشتیبانی", callback_data="support")],
        [InlineKeyboardButton("📢 کانال", callback_data="channel")],
    ])

def admin_keyboard(role="owner"):
    rows = [
        [InlineKeyboardButton("📊 داشبورد",callback_data="admin_dashboard"), InlineKeyboardButton("👥 کاربران",callback_data="admin_users")],
        [InlineKeyboardButton("📦 سرویس‌ها",callback_data="admin_services"), InlineKeyboardButton("🔑 مرکز کانفیگ",callback_data="admin_configs")],
        [InlineKeyboardButton("🧪 مرکز تست رایگان",callback_data="admin_test"), InlineKeyboardButton("⚡ مرکز عملیات",callback_data="admin_ops")],
        [InlineKeyboardButton("💰 درخواست شارژ",callback_data="admin_topups"), InlineKeyboardButton("🛒 سفارش‌ها",callback_data="admin_orders")],
        [InlineKeyboardButton("💳 تراکنش‌ها",callback_data="admin_transactions"), InlineKeyboardButton("🎁 تخفیف‌ها",callback_data="admin_discounts")],
        [InlineKeyboardButton("⭐ دعوت‌ها",callback_data="admin_referral"), InlineKeyboardButton("🎫 پشتیبانی",callback_data="admin_support")],
        [InlineKeyboardButton("📢 ارسال همگانی",callback_data="admin_broadcast"), InlineKeyboardButton("📈 گزارش‌های حرفه‌ای",callback_data="admin_reports_pro")],
        [InlineKeyboardButton("💾 بکاپ / بازیابی",callback_data="admin_backup"), InlineKeyboardButton("⚙️ تنظیمات",callback_data="admin_settings")],
    ]
    if role == "owner":
        rows.append([InlineKeyboardButton("🛡️ دسترسی مدیران",callback_data="admin_staff"), InlineKeyboardButton("🧾 لاگ مدیریت",callback_data="admin_audit")])
    return InlineKeyboardMarkup(rows)

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
# CONFIG INVENTORY / DELIVERY
# =========================================================

def normalize_service_inventory(service):
    """Keep config inventory backward-compatible."""
    pool = service.get("config_pool")
    if not isinstance(pool, list):
        service["config_pool"] = []
        return service

    normalized = []
    for item in pool:
        if isinstance(item, str):
            normalized.append({
                "id": uid("cfg"),
                "config": item.strip(),
                "status": "free",
                "assigned_to": None,
                "assigned_service_id": None,
                "assigned_at": None,
            })
        elif isinstance(item, dict):
            cfg = str(item.get("config", "")).strip()
            if not cfg:
                continue
            item.setdefault("id", uid("cfg"))
            item.setdefault("status", "free")
            item.setdefault("assigned_to", None)
            item.setdefault("assigned_service_id", None)
            item.setdefault("assigned_at", None)
            normalized.append(item)

    service["config_pool"] = normalized
    return service


def normalize_inventory():
    for service in data.get("services", {}).values():
        normalize_service_inventory(service)


def free_config_count(service):
    normalize_service_inventory(service)
    return sum(
        1 for x in service.get("config_pool", [])
        if x.get("status") == "free"
    )


def total_config_count(service):
    normalize_service_inventory(service)
    return len(service.get("config_pool", []))


def config_already_exists(config_text):
    target = config_text.strip()
    if not target:
        return True

    for service in data["services"].values():
        normalize_service_inventory(service)
        for item in service.get("config_pool", []):
            if item.get("config", "").strip() == target:
                return True
    return False


def claim_free_config(service, user_id, owned_service_id):
    normalize_service_inventory(service)

    for item in service.get("config_pool", []):
        if item.get("status") == "free" and item.get("config"):
            item["status"] = "assigned"
            item["assigned_to"] = user_id
            item["assigned_service_id"] = owned_service_id
            item["assigned_at"] = now_iso()
            return item["config"]

    return None


def release_config(service, config_id):
    normalize_service_inventory(service)

    for item in service.get("config_pool", []):
        if item.get("id") == config_id:
            item["status"] = "free"
            item["assigned_to"] = None
            item["assigned_service_id"] = None
            item["assigned_at"] = None
            return True
    return False


async def admin_config_inventory(query, service_id):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return

    service = data["services"].get(service_id)
    if not service:
        await query.edit_message_text("❌ پلن پیدا نشد.", reply_markup=back_admin())
        return

    normalize_service_inventory(service)
    total = total_config_count(service)
    free = free_config_count(service)
    sold = total - free

    await query.edit_message_text(
        f"🔑 موجودی کانفیگ\n\n"
        f"📦 پلن: {esc(service.get('name', '-'))}\n"
        f"📊 کل: {total}\n"
        f"🟢 آزاد: {free}\n"
        f"🔴 تحویل‌شده: {sold}\n\n"
        "کانفیگ‌های آزاد بعد از خرید موفق به‌صورت خودکار تحویل داده می‌شوند.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن کانفیگ", callback_data=f"add_configs:{service_id}")],
            [InlineKeyboardButton("📋 لیست کانفیگ‌ها", callback_data=f"list_configs:{service_id}")],
            [InlineKeyboardButton("🧹 حذف کانفیگ‌های آزاد", callback_data=f"clear_free_configs:{service_id}")],
            [InlineKeyboardButton("🔙 پلن", callback_data=f"aservice:{service_id}")],
        ]),
    )


async def admin_config_list(query, service_id):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return

    service = data["services"].get(service_id)
    if not service:
        await query.edit_message_text("❌ پلن پیدا نشد.", reply_markup=back_admin())
        return

    normalize_service_inventory(service)
    items = service.get("config_pool", [])
    if not items:
        body = "هنوز هیچ کانفیگی وارد نشده."
    else:
        rows = []
        for item in items[:40]:
            state = "🟢 آزاد" if item.get("status") == "free" else "🔴 تحویل"
            masked = mask_config(item.get("config", ""))
            rows.append(f"• {state} | {item.get('id')} | {masked}")
        body = "\n".join(rows)

    await query.edit_message_text(
        f"📋 کانفیگ‌های {esc(service.get('name', '-'))}\n\n{body}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 موجودی", callback_data=f"config_inventory:{service_id}")],
        ]),
    )


def mask_config(config):
    if not config:
        return "-"
    if len(config) <= 18:
        return config[:5] + "•••"
    return config[:9] + "•••" + config[-6:]


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
    # کاربران فقط پلن‌هایی را می‌بینند که فعال هستند و حداقل یک کانفیگ آزاد دارند.
    services = [
        service for service in active_services()
        if free_config_count(service) > 0
    ]

    if not services:
        await query.edit_message_text(
            "🛒 فروشگاه NovaLinkVPN\n\n"
            "در حال حاضر هیچ پلن قابل خریدی موجود نیست.\n"
            "لطفاً بعداً دوباره بررسی کن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="shop")],
                [InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")],
            ]),
        )
        return

    rows = []
    for service in services[:40]:
        stock = free_config_count(service)
        rows.append([
            InlineKeyboardButton(
                f"🟢 {service.get('name', 'پلن')} | موجود: {stock}",
                callback_data=f"view_service:{service['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton("🔄 بروزرسانی", callback_data="shop"),
        InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"),
    ])

    await query.edit_message_text(
        "🛒 فروشگاه NovaLinkVPN\n\n"
        "فقط سرویس‌های دارای موجودی نمایش داده می‌شوند.\n"
        "یک سرویس را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# =========================================================
# PURCHASE
# =========================================================

async def view_service(query, service_id):
    service = data["services"].get(service_id)

    if (
        not service
        or not service.get("active", True)
        or free_config_count(service) <= 0
    ):
        await query.edit_message_text(
            "❌ این سرویس در حال حاضر موجود نیست.",
            reply_markup=back_home(),
        )
        return

    text = (
        f"📦 {esc(service.get('name', ''))}\n\n"
        f"💾 حجم: {service.get('traffic_gb', 0)} GB\n"
        f"🌍 سرور: {esc(service.get('server', '-'))}\n"
        f"⏳ مدت: {service.get('duration_days', 0)} روز\n"
        f"💰 قیمت: {money(service.get('price', 0))} تومان\n"
        f"🟢 موجودی قابل فروش: {free_config_count(service)} عدد\n\n"
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

    if free_config_count(service) <= 0:
        await query.edit_message_text(
            "❌ موجودی کانفیگ این پلن تمام شده است.\n\n"
            "لطفاً یک پلن دیگر انتخاب کن یا منتظر شارژ موجودی باش.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 فروشگاه", callback_data="shop")],
                [InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")],
            ]),
        )
        return

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

    config_text = claim_free_config(service, user_id, service_id_owned)
    if not config_text:
        user["balance"] += price
        await query.edit_message_text(
            "❌ هنگام اختصاص کانفیگ خطایی رخ داد؛ مبلغ به کیف پول برگردانده شد.",
            reply_markup=back_home(),
        )
        return

    config_item_id = next(
        (x.get("id") for x in service.get("config_pool", [])
         if x.get("assigned_service_id") == service_id_owned),
        None,
    )

    owned = {
        "id": service_id_owned,
        "user_id": user_id,
        "plan_id": service_id,
        "plan_name": service.get("name", ""),
        "traffic_gb": service.get("traffic_gb", 0),
        "server": service.get("server", ""),
        "config": config_text,
        "config_item_id": config_item_id,
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
        "🔑 کانفیگ اختصاصی تو:\n\n"
        f"<code>{esc(config_text)}</code>\n\n"
        "این کانفیگ به‌صورت اختصاصی از موجودی پلن رزرو شد.",
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
        parse_mode="HTML",
    )



async def renew_service(query, user_id, owned_id):
    owned = data["services"].get(owned_id)
    if not owned or owned.get("user_id") != user_id:
        await query.edit_message_text("❌ سرویس پیدا نشد.", reply_markup=back_home())
        return

    plan_id = owned.get("plan_id")
    plan = data["services"].get(plan_id)
    user = data["users"].get(str(user_id))

    if not plan or not plan.get("active", True) or not user:
        await query.edit_message_text("❌ پلن تمدید در دسترس نیست.", reply_markup=back_home())
        return

    price = int(plan.get("price", 0))
    if user.get("balance", 0) < price:
        await query.edit_message_text(
            f"❌ موجودی کافی نیست.\n\nقیمت تمدید: {money(price)} تومان\n"
            f"موجودی: {money(user.get('balance', 0))} تومان",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 کیف پول", callback_data="wallet")],
                [InlineKeyboardButton("🔙 سرویس", callback_data=f"owned_service:{owned_id}")],
            ]),
        )
        return

    current = parse_dt(owned.get("expires_at"))
    now = datetime.now(timezone.utc)
    base = current if current and current > now else now
    owned["expires_at"] = (base + timedelta(days=int(plan.get("duration_days", 30)))).isoformat()
    owned["status"] = "active"

    user["balance"] -= price

    order_id = uid("ord")
    tx_id = uid("tx")
    data["orders"][order_id] = {
        "id": order_id,
        "user_id": user_id,
        "service_id": plan_id,
        "owned_service_id": owned_id,
        "amount": price,
        "status": "paid",
        "created_at": now_iso(),
        "payment_method": "wallet",
        "type": "renewal",
    }
    data["transactions"][tx_id] = {
        "id": tx_id,
        "user_id": user_id,
        "type": "renewal",
        "amount": -price,
        "description": f"تمدید {plan.get('name', '')}",
        "created_at": now_iso(),
        "reference": order_id,
    }
    user["order_ids"].append(order_id)
    user["transaction_ids"].append(tx_id)
    data["stats"]["total_sales"] += 1
    data["stats"]["total_revenue"] += price
    await save_data()

    await query.edit_message_text(
        "✅ تمدید با موفقیت انجام شد!\n\n"
        f"📦 {esc(plan.get('name', 'سرویس'))}\n"
        f"💰 {money(price)} تومان\n"
        f"⏳ انقضای جدید: {parse_dt(owned['expires_at']).strftime('%Y-%m-%d %H:%M')}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 سرویس من", callback_data=f"owned_service:{owned_id}")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")],
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
                ),
                InlineKeyboardButton(
                    "📋 دریافت کانفیگ",
                    callback_data=f"send_config:{service_id}",
                ),
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
    owner_id = str(ADMIN_ID) if ADMIN_ID else "-"

    await query.edit_message_text(
        "💳 کیف پول\n\n"
        f"💰 موجودی: {money(user.get('balance', 0))} تومان\n\n"
        "💳 افزایش موجودی آنلاین هنوز فعال نشده است.\n"
        "در نسخه بعدی امکان اتصال به درگاه پرداخت اضافه خواهد شد.\n\n"
        "📩 برای افزایش موجودی فعلاً با مدیریت هماهنگ کن.\n"
        f"🆔 آیدی مدیریت: {owner_id}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📩 ارتباط با مدیریت",
                    url=f"tg://user?id={owner_id}" if ADMIN_ID else CHANNEL_URL,
                )
            ],
            [InlineKeyboardButton("📜 تراکنش‌ها", callback_data="transactions")],
            [InlineKeyboardButton("🔙 حساب کاربری", callback_data="account")],
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


async def buy_with_wallet(query, user_id, service_id):
    """Concurrency-safe purchase entry point: prevents double assignment of one config."""
    if purchase_lock.locked():
        await query.answer("⏳ یک خرید دیگر در حال پردازش است؛ چند لحظه صبر کن.", show_alert=True)
        return
    async with purchase_lock:
        return await _buy_with_wallet_locked(query, user_id, service_id)


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
        f"🎁 کد تخفیف: {len(data['discounts'])}\n\n"
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
                "🧪 مدیریت تست کاربر",
                callback_data=f"test_user_view:{user_id}",
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
        f"{test_status_for_account(user_id)}\n"
        f"🎁 مجوز تست اضافه: {u.get('test_extra_credits', 0)}\n"
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
        f"🚦 وضعیت: {status}\n"
        f"🔑 موجودی کانفیگ: {free_config_count(s)} آزاد / {total_config_count(s)} کل\n\n"
        f"{esc(s.get('description', ''))}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔑 مدیریت کانفیگ‌ها",
                    callback_data=f"config_inventory:{service_id}",
                )
            ],
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


async def admin_ops_center(query):
    if not can_admin(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    pending = sum(1 for x in data.get("topups",{}).values() if x.get("status") == "pending")
    open_tickets = sum(1 for x in data.get("tickets",{}).values() if x.get("status") == "open")
    low = []
    total_free = 0
    for s in data.get("services",{}).values():
        free = free_config_count(s)
        total_free += free
        if free <= int(data.get("settings",{}).get("low_stock_threshold",3)):
            low.append((s.get("name","-"), free))
    rows = [
        [InlineKeyboardButton(f"💰 درخواست شارژ ({pending})", callback_data="admin_topups")],
        [InlineKeyboardButton(f"🎫 تیکت باز ({open_tickets})", callback_data="admin_support")],
        [InlineKeyboardButton(f"🟢 موجودی آزاد کل: {total_free}", callback_data="config_free")],
        [InlineKeyboardButton(f"⚠️ هشدار موجودی ({len(low)})", callback_data="admin_stock_alerts")],
        [InlineKeyboardButton("🔑 مرکز کانفیگ", callback_data="admin_configs")],
        [InlineKeyboardButton("💾 بکاپ فوری", callback_data="backup_now")],
        [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")],
    ]
    alert_text = "✅ هیچ پلنی در محدوده کم‌موجودی نیست."
    if low:
        alert_text = "\n".join(f"• {esc(name)}: {count} عدد" for name, count in low[:20])
    await query.edit_message_text(
        "⚡ مرکز عملیات NovaLinkVPN\n\n"
        f"💰 درخواست شارژ در انتظار: {pending}\n"
        f"🎫 تیکت باز: {open_tickets}\n"
        f"🔑 کانفیگ آزاد: {total_free}\n\n"
        "⚠️ کمبود موجودی:\n" + alert_text,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def admin_stock_alerts(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    threshold = int(data.get("settings",{}).get("low_stock_threshold",3))
    items=[]
    for sid, s in data.get("services",{}).items():
        free=free_config_count(s)
        if free <= threshold:
            items.append((free, s.get("name","-"), sid))
    items.sort(key=lambda x:(x[0], x[1]))
    rows=[]
    for free,name,sid in items[:25]:
        rows.append([InlineKeyboardButton(f"⚠️ {name} | {free} آزاد", callback_data=f"config_inventory:{sid}")])
    if not rows:
        text="✅ همه پلن‌ها موجودی مناسبی دارند."
    else:
        text=f"⚠️ هشدار موجودی\n\nحد هشدار: {threshold}\n\nپلن‌ها:"
    rows.append([InlineKeyboardButton("🔙 مرکز عملیات", callback_data="admin_ops")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def admin_configs_home(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return

    total = 0
    free = 0
    sold = 0
    for service in data["services"].values():
        normalize_service_inventory(service)
        total += total_config_count(service)
        free += free_config_count(service)
    sold = total - free

    await query.edit_message_text(
        "🔑 مدیریت کانفیگ‌ها\n\n"
        f"📦 کل کانفیگ‌ها: {total}\n"
        f"🟢 آماده فروش: {free}\n"
        f"🔴 فروخته‌شده: {sold}\n\n"
        "از اینجا موجودی فروش و سوابق تحویل را جداگانه مدیریت کن.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 کانفیگ‌های آماده فروش", callback_data="config_free")],
            [InlineKeyboardButton("🔴 کانفیگ‌های فروخته‌شده", callback_data="config_sold")],
            [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")],
        ]),
    )


async def admin_configs_free(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return

    rows = []
    total_free = 0
    for service in list(data["services"].values())[:60]:
        free = free_config_count(service)
        if free <= 0:
            continue
        total_free += free
        rows.append([
            InlineKeyboardButton(
                f"🟢 {service.get('name', 'پلن')} | {free} آماده فروش",
                callback_data=f"config_inventory:{service['id']}",
            )
        ])

    if not rows:
        body = "هیچ کانفیگ آزادی برای فروش وجود ندارد."
    else:
        body = f"تعداد کانفیگ آماده فروش: {total_free}\n\nبرای هر پلن وارد مدیریت موجودی شو:"

    rows.append([InlineKeyboardButton("🔙 مدیریت کانفیگ‌ها", callback_data="admin_configs")])
    await query.edit_message_text(
        "🟢 کانفیگ‌های آماده فروش\n\n" + body,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_configs_sold(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return

    sold_items = []
    for service in data["services"].values():
        normalize_service_inventory(service)
        for item in service.get("config_pool", []):
            if item.get("status") == "assigned":
                sold_items.append((service, item))

    sold_items.sort(key=lambda pair: pair[1].get("assigned_at", ""), reverse=True)

    if not sold_items:
        text = "🔴 کانفیگ‌های فروخته‌شده\n\nهنوز کانفیگی تحویل داده نشده است."
    else:
        lines = [f"🔴 کانفیگ‌های فروخته‌شده\n\nتعداد: {len(sold_items)}\n"]
        for service, item in sold_items[:40]:
            lines.append(
                f"📦 {esc(service.get('name', '-'))}\n"
                f"👤 کاربر: {item.get('assigned_to', '-')}\n"
                f"🆔 کانفیگ: {item.get('id', '-')}\n"
                f"🕒 زمان تحویل: {item.get('assigned_at', '-')}\n"
                f"🔑 {esc(mask_config(item.get('config', '')))}\n"
            )
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 بروزرسانی", callback_data="config_sold")],
            [InlineKeyboardButton("🔙 مدیریت کانفیگ‌ها", callback_data="admin_configs")],
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
        f"🎫 حذف تیکت بسته: {s.get('closed_ticket_retention_hours', 72)} ساعت",
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
        "renew:", "config_"
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

    if data_key == "admin_configs":
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_configs_home(query)
        return

    if data_key == "config_free":
        await admin_configs_free(query)
        return

    if data_key == "config_sold":
        await admin_configs_sold(query)
        return

    if data_key == "admin_services":
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_services(query)
        return

    if data_key.startswith("aservice:"):
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        await admin_service_view(query, data_key.split(":", 1)[1])
        return

    if data_key.startswith("config_inventory:"):
        await admin_config_inventory(query, data_key.split(":", 1)[1])
        return

    if data_key.startswith("list_configs:"):
        await admin_config_list(query, data_key.split(":", 1)[1])
        return

    if data_key.startswith("add_configs:"):
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        sid = data_key.split(":", 1)[1]
        context.user_data["state"] = f"add_configs:{sid}"
        await query.edit_message_text(
            "➕ افزودن کانفیگ\n\n"
            "هر کانفیگ را در یک خط جداگانه بفرست.\n"
            "می‌توانی ۱ تا چند صد کانفیگ را یکجا ارسال کنی.\n\n"
            "مثال:\n"
            "vless://...\n"
            "vless://...\n"
            "vmess://...\n\n"
            "کانفیگ تکراری دوباره اضافه نمی‌شود.\n"
            "/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if data_key.startswith("clear_free_configs:"):
        if not can_manage_services(query.from_user.id):
            await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
            return
        sid = data_key.split(":", 1)[1]
        service = data["services"].get(sid)
        if service:
            normalize_service_inventory(service)
            before = len(service["config_pool"])
            service["config_pool"] = [
                x for x in service["config_pool"] if x.get("status") != "free"
            ]
            removed = before - len(service["config_pool"])
            await save_data()
            await query.answer(f"{removed} کانفیگ آزاد حذف شد.", show_alert=True)
        await admin_config_inventory(query, sid)
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
        tid = data_key.split(":", 1)[1]
        ticket = data["tickets"].get(tid)
        if ticket:
            ticket["status"] = "closed"
            ticket["closed_at"] = now_iso()
            ticket["updated_at"] = now_iso()
            await save_data()
            try:
                await context.bot.send_message(
                    chat_id=int(ticket["user_id"]),
                    text=(
                        "✅ تیکت شما بسته شد.\n\n"
                        "این تیکت تا ۷۲ ساعت نگهداری می‌شود و سپس "
                        "در صورت بسته‌بودن به‌صورت خودکار حذف خواهد شد."
                    ),
                )
            except Exception:
                pass
        await query.edit_message_text("✅ تیکت بسته شد.", reply_markup=back_admin())
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

    if data_key.startswith("send_config:"):
        owned = data["services"].get(data_key.split(":", 1)[1])
        if not owned or owned.get("user_id") != query.from_user.id:
            await query.answer("دسترسی ندارید.", show_alert=True)
            return
        config_text = owned.get("config") or "کانفیگی برای این سرویس ثبت نشده است."
        await query.message.reply_text(
            f"🔑 کانفیگ سرویس {esc(owned.get('plan_name', ''))}:\n\n"
            f"<code>{esc(config_text)}</code>",
            parse_mode="HTML",
        )
        await query.answer("کانفیگ ارسال شد ✅")
        return

    if data_key.startswith("renew:"):
        await renew_service(query, query.from_user.id, data_key.split(":", 1)[1])
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

    # Admin config inventory import
    if state and state.startswith("add_configs:"):
        if not can_manage_services(update.effective_user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return

        sid = state.split(":", 1)[1]
        service = data["services"].get(sid)
        if not service:
            context.user_data.clear()
            await update.message.reply_text("❌ پلن پیدا نشد.")
            return

        normalize_service_inventory(service)
        candidates = [x.strip() for x in text.splitlines() if x.strip()]
        if not candidates:
            await update.message.reply_text("❌ حداقل یک کانفیگ معتبر بفرست.")
            return

        added = 0
        duplicate = 0
        for cfg in candidates:
            if config_already_exists(cfg):
                duplicate += 1
                continue
            service["config_pool"].append({
                "id": uid("cfg"),
                "config": cfg,
                "status": "free",
                "assigned_to": None,
                "assigned_service_id": None,
                "assigned_at": None,
                "created_at": now_iso(),
            })
            added += 1

        context.user_data.clear()
        await save_data()

        await update.message.reply_text(
            f"✅ موجودی کانفیگ بروزرسانی شد.\n\n"
            f"➕ اضافه‌شده: {added}\n"
            f"♻️ تکراری: {duplicate}\n"
            f"📦 موجودی آزاد فعلی: {free_config_count(service)}",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
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
            "config_pool": [],
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
    """Handle JSON backup uploads for Owner restore."""
    if not update.message or not update.message.document:
        return

    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ فقط Owner می‌تواند Restore انجام دهد.")
        return

    doc = update.message.document
    filename = (doc.file_name or "").strip()
    is_json = filename.lower().endswith(".json")
    restore_mode = context.user_data.get("state") == "restore_file"
    looks_like_backup = filename.lower().startswith("novalinkvpn_backup")

    if not is_json:
        await update.message.reply_text(
            "❌ فقط فایل JSON قابل بازیابی است.\n\n"
            "فایل بکاپ باید با پسوند .json باشد."
        )
        return

    if not restore_mode and not looks_like_backup:
        await update.message.reply_text(
            "📄 این فایل دریافت شد، اما حالت بازیابی فعال نیست.\n\n"
            "اول از پنل مدیریت وارد «💾 بکاپ / بازیابی» → «📤 حالت بازیابی» شو."
        )
        return

    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await update.message.reply_text(
            "❌ فایل بیش از حد بزرگ است. حداکثر اندازه بکاپ 10MB است."
        )
        return

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        content = await tg_file.download_as_bytearray()
        restored = restore_from_bytes(bytes(content))
    except Exception as e:
        logger.exception("Restore validation failed")
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "❌ بازیابی انجام نشد.\n\n"
            f"جزئیات: {e}\n\n"
            "فایل باید همان JSON بکاپی باشد که ربات ساخته است.",
            reply_markup=admin_keyboard("owner"),
        )
        return

    try:
        await make_backup("before_restore")
    except Exception as e:
        logger.exception("Could not create pre-restore backup")
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "❌ برای امنیت، بکاپ فعلی قبل از Restore ساخته نشد؛\n"
            "بنابراین Restore انجام نشد.\n\n"
            f"خطا: {e}",
            reply_markup=admin_keyboard("owner"),
        )
        return

    global data
    data = restored

    try:
        normalize_data()
        normalize_test_system()
        save_data_sync()
    except Exception as e:
        logger.exception("Restore normalization/save failed")
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "❌ داده بکاپ خوانده شد اما هنگام ثبت نهایی خطا رخ داد.\n\n"
            f"خطا: {e}",
            reply_markup=admin_keyboard("owner"),
        )
        return

    context.user_data.clear()
    await update.message.reply_text(
        "✅ Restore با موفقیت انجام شد!\n\n"
        f"📁 فایل: {filename or 'backup.json'}\n"
        f"👥 کاربران: {len(data.get('users', {}))}\n"
        f"📦 سرویس‌ها: {len(data.get('services', {}))}\n"
        f"🧪 کانفیگ‌های تست: {len(data.get('test_configs', {}))}\n\n"
        "🛡️ یک بکاپ ایمنی از اطلاعات قبلی نیز قبل از Restore ساخته شد.",
        reply_markup=admin_keyboard("owner"),
    )


# =========================================================
# TICKET AUTO CLEANUP
# =========================================================

async def ticket_cleanup_worker():
    while True:
        try:
            retention_hours = int(data["settings"].get("closed_ticket_retention_hours", 72))
            cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
            removed = []

            for tid, ticket in list(data["tickets"].items()):
                if ticket.get("status") != "closed":
                    continue

                closed_at = parse_dt(ticket.get("closed_at") or ticket.get("updated_at"))
                if closed_at and closed_at <= cutoff:
                    removed.append(tid)
                    user_id = str(ticket.get("user_id"))
                    user = data["users"].get(user_id)
                    if user:
                        user["ticket_ids"] = [
                            x for x in user.get("ticket_ids", []) if x != tid
                        ]

            for tid in removed:
                data["tickets"].pop(tid, None)

            if removed:
                await save_data()
                logger.info("Auto-removed %s closed tickets.", len(removed))

            await asyncio.sleep(3600)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Ticket cleanup error.")
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
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if ADMIN_ID == 0:
        logger.warning(
            "ADMIN_ID is not configured. Admin panel will be unavailable."
        )

    global application

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




# =========================================================
# NOVALINKVPN EXPANSION PACK v2
# Professional UX, wallet requests, notifications, analytics,
# audit trail, safer purchases, advanced config center,
# broadcast targeting, favorites, and maintenance controls.
# Still single-file + JSON only.
# =========================================================

BASE_DEFAULT_DATA = default_data
BASE_NORMALIZE_DATA = normalize_data
BASE_USER_KEYBOARD = user_keyboard
BASE_ADMIN_KEYBOARD = admin_keyboard
BASE_START = start
BASE_SHOW_WALLET = show_wallet
BASE_SHOW_ACCOUNT = show_account
BASE_SHOW_MY_SERVICES = show_my_services
BASE_SHOW_OWNED_SERVICE = show_owned_service
BASE_VIEW_SERVICE = view_service
BASE_BUY_WITH_WALLET = buy_with_wallet
BASE_RENEW_SERVICE = renew_service
BASE_SHOW_SHOP = show_shop
BASE_ADMIN_CONFIGS_HOME = admin_configs_home
BASE_ADMIN_CONFIGS_FREE = admin_configs_free
BASE_ADMIN_CONFIGS_SOLD = admin_configs_sold
BASE_ADMIN_REPORTS = admin_reports
BASE_APPLY_DISCOUNT = apply_discount

BASE_CALLBACK_HANDLER = callback_handler
BASE_MESSAGE_HANDLER = message_handler


# =========================================================
# 🧪 NOVALINKVPN FREE TEST SYSTEM v1
# Separate inventory, one-test-per-user by default,
# admin-granted extra test credits, history, statistics,
# secure ownership checks, and JSON backup compatibility.
# =========================================================

TEST_DEFAULT_DURATION_HOURS = 2
TEST_MAX_DURATION_HOURS = 168


def _test_settings():
    return data.setdefault("settings", {})


def normalize_test_data():
    s = _test_settings()
    data.setdefault("test_configs", {})
    data.setdefault("test_claims", {})

    s.setdefault("test_enabled", True)
    s.setdefault("test_duration_hours", TEST_DEFAULT_DURATION_HOURS)
    s.setdefault("test_one_per_user", True)
    s.setdefault("test_allowance_mode", "admin_grant")
    s.setdefault("test_low_stock_threshold", 5)

    try:
        s["test_duration_hours"] = max(1, min(int(s.get("test_duration_hours", TEST_DEFAULT_DURATION_HOURS)), TEST_MAX_DURATION_HOURS))
    except Exception:
        s["test_duration_hours"] = TEST_DEFAULT_DURATION_HOURS

    for key, u in data.get("users", {}).items():
        u.setdefault("test_claim_count", 0)
        u.setdefault("test_extra_credits", 0)
        u.setdefault("test_history_ids", [])

    # Normalize test inventory records.
    normalized = {}
    for key, item in list(data.get("test_configs", {}).items()):
        if isinstance(item, str):
            cfg = item.strip()
            if not cfg:
                continue
            item = {
                "id": str(key) if key else uid("testcfg"),
                "config": cfg,
                "status": "free",
                "assigned_to": None,
                "claim_id": None,
                "assigned_at": None,
                "created_at": now_iso(),
            }
        elif isinstance(item, dict):
            cfg = str(item.get("config", "")).strip()
            if not cfg:
                continue
            item["config"] = cfg
            item.setdefault("id", str(key) if key else uid("testcfg"))
            item.setdefault("status", "free")
            item.setdefault("assigned_to", None)
            item.setdefault("claim_id", None)
            item.setdefault("assigned_at", None)
            item.setdefault("created_at", now_iso())
        else:
            continue
        normalized[str(item["id"])] = item
    data["test_configs"] = normalized


def test_total_count():
    normalize_test_data()
    return len(data["test_configs"])


def test_free_count():
    normalize_test_data()
    return sum(1 for x in data["test_configs"].values() if x.get("status") == "free" and x.get("config"))


def test_assigned_count():
    normalize_test_data()
    return sum(1 for x in data["test_configs"].values() if x.get("status") == "assigned")


def test_user_claims(user_id):
    normalize_test_data()
    return [
        x for x in data["test_claims"].values()
        if int(x.get("user_id", 0)) == int(user_id)
    ]


def test_active_claim(user_id):
    normalize_test_data()
    now = datetime.now(timezone.utc)
    active = None
    for claim in data["test_claims"].values():
        if int(claim.get("user_id", 0)) != int(user_id):
            continue
        if claim.get("status") != "active":
            continue
        exp = parse_dt(claim.get("expires_at"))
        if exp and exp > now:
            active = claim
            break
        claim["status"] = "expired"
    return active


def test_remaining(claim):
    if not claim:
        return "-"
    exp = parse_dt(claim.get("expires_at"))
    if not exp:
        return "-"
    sec = int((exp - datetime.now(timezone.utc)).total_seconds())
    if sec <= 0:
        return "منقضی شده"
    h, rem = divmod(sec, 3600)
    m = rem // 60
    if h:
        return f"{h} ساعت و {m} دقیقه"
    return f"{m} دقیقه"


def test_config_duplicate(config_text):
    target = str(config_text or "").strip()
    if not target:
        return True
    normalize_test_data()
    for item in data["test_configs"].values():
        if str(item.get("config", "")).strip() == target:
            return True
    for service in data.get("services", {}).values():
        normalize_service_inventory(service)
        for item in service.get("config_pool", []):
            if str(item.get("config", "")).strip() == target:
                return True
    return False


def find_user_identifier(identifier):
    raw = str(identifier or "").strip()
    raw = raw.lstrip("@")
    if not raw:
        return None
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return data.get("users", {}).get(str(raw))
    target = raw.lower()
    for u in data.get("users", {}).values():
        if str(u.get("username", "")).lower() == target:
            return u
    return None


def test_grant_status(user_id):
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return None
    claims = test_user_claims(user_id)
    active = test_active_claim(user_id)
    total = len(claims)
    extra = int(u.get("test_extra_credits", 0))
    allowed_total = 1 + extra if _test_settings().get("test_one_per_user", True) else 10**9
    remaining_entitlement = max(0, allowed_total - total)
    return {
        "claims": claims,
        "active": active,
        "total": total,
        "extra": extra,
        "allowed_total": allowed_total,
        "remaining_entitlement": remaining_entitlement,
    }


def _claim_test_config_sync(user_id, reason="standard", granted_credit_used=False):
    normalize_test_data()
    settings = _test_settings()
    uid_key = str(user_id)
    user = data.get("users", {}).get(uid_key)
    if not user:
        return False, None, "user_not_found"
    if not settings.get("test_enabled", True):
        return False, None, "disabled"

    active = test_active_claim(user_id)
    if active:
        return False, active, "already_active"

    user.setdefault("test_claim_count", 0)
    user.setdefault("test_extra_credits", 0)
    user.setdefault("test_history_ids", [])

    prior = int(user.get("test_claim_count", 0))
    extra = int(user.get("test_extra_credits", 0))

    if settings.get("test_one_per_user", True):
        if prior >= 1:
            if extra <= 0:
                return False, None, "limit_reached"
            user["test_extra_credits"] = extra - 1
            granted_credit_used = True
    
    free_item = None
    for item in data["test_configs"].values():
        if item.get("status") == "free" and item.get("config"):
            free_item = item
            break
    if not free_item:
        if granted_credit_used:
            user["test_extra_credits"] = int(user.get("test_extra_credits", 0)) + 1
        return False, None, "out_of_stock"

    try:
        duration = max(1, min(int(settings.get("test_duration_hours", TEST_DEFAULT_DURATION_HOURS)), TEST_MAX_DURATION_HOURS))
    except Exception:
        duration = TEST_DEFAULT_DURATION_HOURS

    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(hours=duration)
    claim_id = uid("test")
    config_id = str(free_item["id"])

    free_item["status"] = "assigned"
    free_item["assigned_to"] = int(user_id)
    free_item["claim_id"] = claim_id
    free_item["assigned_at"] = now.isoformat()

    claim = {
        "id": claim_id,
        "user_id": int(user_id),
        "config_id": config_id,
        "config": free_item["config"],
        "status": "active",
        "reason": reason,
        "granted_credit_used": bool(granted_credit_used),
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "duration_hours": duration,
    }
    data["test_claims"][claim_id] = claim
    user["test_claim_count"] = prior + 1
    user["test_history_ids"].append(claim_id)
    return True, claim, "success"


async def claim_test_config(user_id, reason="standard"):
    async with test_lock:
        result = _claim_test_config_sync(user_id, reason=reason)
        if result[0]:
            add_audit(user_id, "test_claim", result[1].get("id", ""), reason)
            await save_data()
        return result


async def send_test_config_message(query, user_id):
    claim = test_active_claim(user_id)
    if not claim:
        await query.answer("تست فعال ندارید یا تست شما تمام شده است.", show_alert=True)
        return
    config = claim.get("config", "")
    await query.message.reply_text(
        "🔐 کانفیگ تست NovaLinkVPN\n\n"
        f"⏳ زمان باقی‌مانده: {test_remaining(claim)}\n"
        f"📅 پایان: {claim.get('expires_at', '-')}\n\n"
        "📋 کانفیگ:\n"
        f"<code>{esc(config)}</code>\n\n"
        "⚠️ این کانفیگ مخصوص تست است.",
        parse_mode="HTML",
    )
    await query.answer("کانفیگ تست ارسال شد ✅")


async def user_test_page(query, user_id):
    normalize_test_data()
    user = data.get("users", {}).get(str(user_id))
    if not user:
        await query.edit_message_text("❌ کاربر پیدا نشد.", reply_markup=back_home())
        return
    claim = test_active_claim(user_id)
    await save_data()
    status = test_grant_status(user_id)
    free = test_free_count()
    duration = _test_settings().get("test_duration_hours", TEST_DEFAULT_DURATION_HOURS)

    if claim:
        await query.edit_message_text(
            "🧪 تست رایگان NovaLinkVPN\n\n"
            "✅ تست فعال داری.\n\n"
            f"⏳ باقی‌مانده: {test_remaining(claim)}\n"
            f"📅 پایان: {claim.get('expires_at', '-')}\n"
            f"🔢 تست دریافت‌شده: {status['total']}\n\n"
            "برای دریافت مجدد، ابتدا باید تست فعلی تمام شود.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 دریافت کانفیگ تست", callback_data="test_config")],
                [InlineKeyboardButton("🔄 بروزرسانی", callback_data="test_menu")],
                [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop")],
                [InlineKeyboardButton("🔙 خانه", callback_data="home")],
            ]),
        )
        return

    if _test_settings().get("test_one_per_user", True) and status["remaining_entitlement"] <= 0:
        await query.edit_message_text(
            "🧪 تست رایگان NovaLinkVPN\n\n"
            "⛔ سهم تست رایگان شما تمام شده است.\n\n"
            f"📊 تعداد تست دریافت‌شده: {status['total']}\n"
            f"🎁 تست اضافه از مدیر: {status['extra']}\n\n"
            "برای استفاده بیشتر، با پشتیبانی تماس بگیر.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎫 پشتیبانی", callback_data="support")],
                [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop")],
                [InlineKeyboardButton("🔙 خانه", callback_data="home")],
            ]),
        )
        return

    if free <= 0:
        await query.edit_message_text(
            "🧪 تست رایگان NovaLinkVPN\n\n"
            "⚠️ موجودی تست در حال حاضر تمام شده است.\n"
            "به‌محض شارژ موجودی دوباره می‌توانی درخواست بدهی.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 بررسی مجدد", callback_data="test_menu")],
                [InlineKeyboardButton("🔙 خانه", callback_data="home")],
            ]),
        )
        return

    next_number = status["total"] + 1
    extra_note = "🎁 این تست با مجوز اضافه فعال می‌شود." if next_number > 1 else "🎁 سهم تست رایگان اولیه شماست."
    await query.edit_message_text(
        "🧪 تست رایگان NovaLinkVPN\n\n"
        "قبل از خرید، اول خودت امتحانش کن. 🚀\n\n"
        f"⏱ مدت تست: {duration} ساعت\n"
        f"📦 موجودی تست: {free} عدد\n"
        f"🔢 تست بعدی شما: شماره {next_number}\n\n"
        f"{extra_note}\n"
        "✅ یک کانفیگ اختصاصی تحویل می‌گیری\n"
        "✅ قبل از خرید می‌توانی کیفیت را بررسی کنی\n"
        "✅ هر کانفیگ فقط یک‌بار در اختیار یک کاربر قرار می‌گیرد\n\n"
        "آماده‌ای؟ 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 دریافت تست رایگان", callback_data="claim_test")],
            [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop")],
            [InlineKeyboardButton("🔙 خانه", callback_data="home")],
        ]),
    )


async def claim_test_button(query, user_id):
    success, claim, reason = await claim_test_config(user_id)
    if not success:
        messages = {
            "disabled": "⛔ سیستم تست رایگان فعلاً غیرفعال است.",
            "already_active": f"✅ یک تست فعال داری. زمان باقی‌مانده: {test_remaining(claim)}",
            "limit_reached": "⛔ سهم تست رایگان شما تمام شده است.\nبرای تست اضافه با پشتیبانی تماس بگیر.",
            "out_of_stock": "⚠️ موجودی تست تمام شده است. بعداً دوباره بررسی کن.",
            "user_not_found": "❌ حساب کاربری پیدا نشد.",
        }
        await query.edit_message_text(
            messages.get(reason, "❌ دریافت تست انجام نشد."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧪 تست رایگان", callback_data="test_menu")],
                [InlineKeyboardButton("🔙 خانه", callback_data="home")],
            ]),
        )
        return

    await query.edit_message_text(
        "🎉 تست رایگان با موفقیت فعال شد!\n\n"
        f"⏱ مدت: {claim.get('duration_hours')} ساعت\n"
        f"📅 پایان: {claim.get('expires_at')}\n\n"
        "کانفیگ اختصاصی برایت آماده است. 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 دریافت کانفیگ تست", callback_data="test_config")],
            [InlineKeyboardButton("🧪 وضعیت تست", callback_data="test_menu")],
            [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop")],
            [InlineKeyboardButton("🔙 خانه", callback_data="home")],
        ]),
    )


async def admin_test_center(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    normalize_test_data()
    active_users = sum(1 for u in data.get("users", {}).values() if test_active_claim(u.get("id", 0)))
    expired = sum(1 for c in data["test_claims"].values() if c.get("status") != "active" or not test_active_claim(c.get("user_id", 0)))
    await query.edit_message_text(
        "🧪 مرکز مدیریت تست رایگان\n\n"
        f"📦 کل کانفیگ تست: {test_total_count()}\n"
        f"🟢 آماده تحویل: {test_free_count()}\n"
        f"🔴 تحویل‌شده: {test_assigned_count()}\n"
        f"👥 کاربران دارای تست فعال: {active_users}\n"
        f"📊 کل تست‌های صادرشده: {len(data['test_claims'])}\n"
        f"⏱ مدت تست: {_test_settings().get('test_duration_hours', TEST_DEFAULT_DURATION_HOURS)} ساعت\n"
        f"⚙️ سیستم: {'🟢 فعال' if _test_settings().get('test_enabled', True) else '🔴 خاموش'}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن کانفیگ تست", callback_data="test_add_configs")],
            [InlineKeyboardButton("🎁 مجوز تست اضافه", callback_data="test_grant")],
            [InlineKeyboardButton("👥 کاربران و سوابق تست", callback_data="test_users")],
            [InlineKeyboardButton("🟢 کانفیگ‌های آزاد", callback_data="test_free_list")],
            [InlineKeyboardButton("🔴 کانفیگ‌های تحویل‌شده", callback_data="test_sold_list")],
            [InlineKeyboardButton("📊 آمار تست", callback_data="test_stats")],
            [InlineKeyboardButton("⚙️ تنظیمات تست", callback_data="test_settings")],
            [InlineKeyboardButton("🗑️ پاک‌کردن موجودی آزاد", callback_data="test_clear_free")],
            [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_home")],
        ]),
    )


async def admin_test_users(query):
    if not can_manage_users(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    rows = []
    ranked = []
    for u in data.get("users", {}).values():
        st = test_grant_status(u.get("id", 0))
        if st and st["total"] > 0:
            ranked.append((u, st))
    ranked.sort(key=lambda x: max([c.get("created_at", "") for c in x[1]["claims"]] or [""]), reverse=True)
    for u, st in ranked[:40]:
        name = (u.get("first_name") or u.get("username") or str(u.get("id")))[:24]
        active = "🟢" if st["active"] else "⚪"
        rows.append([InlineKeyboardButton(
            f"{active} {name} • {st['total']} تست • 🎁 {st['extra']}",
            callback_data=f"test_user_view:{u.get('id')}",
        )])
    if not rows:
        body = "👥 هنوز کسی تست دریافت نکرده است."
    else:
        body = "👥 کاربران تست‌گرفته\n\nفقط کاربرانی که حداقل یک تست گرفته‌اند نمایش داده می‌شوند."
    rows.append([InlineKeyboardButton("🔎 جستجو / مجوز با ID", callback_data="test_grant")])
    rows.append([InlineKeyboardButton("🔙 مدیریت تست", callback_data="admin_test")])
    await query.edit_message_text(body, reply_markup=InlineKeyboardMarkup(rows))


async def admin_test_user_view(query, user_id):
    if not can_manage_users(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    u = data.get("users", {}).get(str(user_id))
    if not u:
        await query.edit_message_text("❌ کاربر پیدا نشد.", reply_markup=back_admin())
        return
    st = test_grant_status(user_id)
    claims = sorted(st["claims"], key=lambda x: x.get("created_at", ""), reverse=True)
    lines = [
        "👤 وضعیت تست کاربر",
        "",
        f"🆔 ID: {u.get('id')}",
        f"🔗 Username: @{u.get('username') or '-'}",
        f"📊 تست دریافت‌شده: {st['total']}",
        f"🎁 مجوز تست اضافه باقی‌مانده: {st['extra']}",
        f"🟢 تست فعال: {'بله' if st['active'] else 'خیر'}",
        "",
        "📜 سوابق اخیر:",
    ]
    for c in claims[:8]:
        lines.append(f"• {c.get('created_at', '-')} | {c.get('status', '-')} | {c.get('reason', 'standard')}")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 یک تست اضافه مجاز کن", callback_data=f"test_grant_user:{user_id}")],
            [InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"test_user_view:{user_id}")],
            [InlineKeyboardButton("🔙 کاربران تست", callback_data="test_users")],
        ]),
    )


async def admin_test_free_list(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    normalize_test_data()
    free = [x for x in data["test_configs"].values() if x.get("status") == "free"]
    lines = [f"🟢 کانفیگ‌های تست آزاد\n\nتعداد: {len(free)}\n"]
    for item in free[:60]:
        lines.append(f"• {item.get('id')} | {mask_config(item.get('config', ''))}")
    if not free:
        lines.append("\nهیچ کانفیگ آزادی وجود ندارد.")
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data="test_free_list")],
        [InlineKeyboardButton("🔙 مدیریت تست", callback_data="admin_test")],
    ]))


async def admin_test_sold_list(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    normalize_test_data()
    sold = sorted([x for x in data["test_configs"].values() if x.get("status") == "assigned"], key=lambda x: x.get("assigned_at", ""), reverse=True)
    lines = [f"🔴 کانفیگ‌های تست تحویل‌شده\n\nتعداد: {len(sold)}\n"]
    for item in sold[:60]:
        lines.append(
            f"• {item.get('id')} | 👤 {item.get('assigned_to', '-')}\n"
            f"  🕒 {item.get('assigned_at', '-')} | 🔑 {mask_config(item.get('config', ''))}"
        )
    if not sold:
        lines.append("هیچ کانفیگی تحویل نشده است.")
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data="test_sold_list")],
        [InlineKeyboardButton("🔙 مدیریت تست", callback_data="admin_test")],
    ]))


async def admin_test_stats(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin())
        return
    normalize_test_data()
    claims = list(data["test_claims"].values())
    active = sum(1 for c in claims if test_active_claim(c.get("user_id", 0)))
    expired = len(claims) - active
    users_received = sum(1 for u in data.get("users", {}).values() if int(u.get("test_claim_count", 0)) > 0)
    extra_granted = sum(int(u.get("test_extra_credits", 0)) for u in data.get("users", {}).values())
    await query.edit_message_text(
        "📊 آمار تست رایگان\n\n"
        f"👥 کاربران تست‌گرفته: {users_received}\n"
        f"🧪 کل تست‌های صادرشده: {len(claims)}\n"
        f"🟢 تست‌های فعال: {active}\n"
        f"⏰ تست‌های تمام‌شده: {expired}\n"
        f"🎁 مجوزهای اضافه باقی‌مانده: {extra_granted}\n"
        f"📦 موجودی آزاد: {test_free_count()}\n"
        f"📦 کل موجودی: {test_total_count()}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 کاربران تست‌گرفته", callback_data="test_users")],
            [InlineKeyboardButton("🔙 مدیریت تست", callback_data="admin_test")],
        ]),
    )


async def admin_test_settings(query):
    if not is_owner(query.from_user.id):
        await query.edit_message_text("⛔ فقط Owner می‌تواند تنظیمات تست را تغییر دهد.", reply_markup=back_admin())
        return
    s = _test_settings()
    await query.edit_message_text(
        "⚙️ تنظیمات تست رایگان\n\n"
        f"🧪 وضعیت: {'🟢 فعال' if s.get('test_enabled', True) else '🔴 خاموش'}\n"
        f"⏱ مدت تست: {s.get('test_duration_hours', TEST_DEFAULT_DURATION_HOURS)} ساعت\n"
        f"👤 محدودیت یک تست پایه: {'🟢 فعال' if s.get('test_one_per_user', True) else '🔴 غیرفعال'}\n"
        "🎁 تست اضافه فقط با مجوز مدیر صادر می‌شود.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 روشن / خاموش", callback_data="test_toggle")],
            [InlineKeyboardButton("⏱ تغییر مدت تست", callback_data="test_duration")],
            [InlineKeyboardButton("👤 محدودیت یک تست", callback_data="test_one_per_user")],
            [InlineKeyboardButton("🔙 مدیریت تست", callback_data="admin_test")],
        ]),
    )


async def admin_test_clear_free(query):
    if not is_owner(query.from_user.id):
        await query.answer("فقط Owner.", show_alert=True)
        return
    normalize_test_data()
    removed = 0
    for key in list(data["test_configs"].keys()):
        if data["test_configs"][key].get("status") == "free":
            data["test_configs"].pop(key, None)
            removed += 1
    await save_data()
    await query.edit_message_text(
        f"🗑️ کانفیگ‌های آزاد تست حذف شدند.\n\n➖ حذف‌شده: {removed}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 مدیریت تست", callback_data="admin_test")]]),
    )


def test_users_with_any_history():
    return [u for u in data.get("users", {}).values() if int(u.get("test_claim_count", 0)) > 0]


def test_dashboard_badge():
    s = _test_settings()
    return f"{'🟢' if s.get('test_enabled', True) else '🔴'} {test_free_count()} آزاد"


def test_status_for_account(user_id):
    st = test_grant_status(user_id)
    if not st:
        return "🧪 تست: بدون سابقه"
    active = st.get("active")
    if active:
        return f"🧪 تست: 🟢 فعال | {test_remaining(active)}"
    return f"🧪 تست: {'✅ قابل دریافت' if st['remaining_entitlement'] > 0 else '⛔ سهم تمام'} | دریافت‌شده: {st['total']}"


def build_test_admin_user_line(u):
    st = test_grant_status(u.get("id", 0)) or {"total": 0, "extra": 0, "active": None}
    return f"{u.get('id')} | {u.get('username') or u.get('first_name') or '-'} | تست: {st['total']} | اضافه: {st['extra']}"


# Final override of default_data below adds the test collections/settings.
def default_data():

    d = BASE_DEFAULT_DATA()
    d.setdefault("meta", {})["version"] = 6
    d.setdefault("settings", {}).update({
        "support_username": PUBLIC_OWNER_USERNAME,
        "test_enabled": True,
        "test_duration_hours": TEST_DEFAULT_DURATION_HOURS,
        "test_one_per_user": True,
        "test_allowance_mode": "admin_grant",
        "test_low_stock_threshold": 5,
        "admin_display_id": str(ADMIN_ID or ""),
        "public_owner_username": PUBLIC_OWNER_USERNAME,
        "public_owner_url": PUBLIC_OWNER_URL,
        "shop_low_stock_note": "موجودی به‌صورت زنده بررسی می‌شود.",
        "shop_title": "🛒 فروشگاه NovaLinkVPN",
        "shop_description": "سرویس‌های موجود را انتخاب کن و خریدت را انجام بده.",
        "maintenance_message": "🛠 ربات موقتاً در حال بروزرسانی است.",
        "low_stock_threshold": 3,
        "notification_retention_days": 30,
        "max_ticket_open": 3,
        "broadcast_delay": 0.05,
        "purchase_confirmation": True,
        "show_stock_to_users": True,
    })
    d.setdefault("topups", {})
    d.setdefault("notifications", {})
    d.setdefault("audit_logs", {})
    d.setdefault("favorites", {})
    d.setdefault("test_configs", {})
    d.setdefault("test_claims", {})
    return d


def normalize_data():
    BASE_NORMALIZE_DATA()
    data.setdefault("topups", {})
    data.setdefault("notifications", {})
    data.setdefault("audit_logs", {})
    data.setdefault("favorites", {})
    s=data.setdefault("settings", {})
    s.setdefault("admin_display_id", str(ADMIN_ID or ""))
    s["support_username"] = PUBLIC_OWNER_USERNAME
    s.setdefault("public_owner_username", PUBLIC_OWNER_USERNAME)
    s.setdefault("public_owner_url", PUBLIC_OWNER_URL)
    s.setdefault("shop_low_stock_note", "موجودی به‌صورت زنده بررسی می‌شود.")
    s.setdefault("shop_title", "🛒 فروشگاه NovaLinkVPN")
    s.setdefault("shop_description", "سرویس‌های موجود را انتخاب کن و خریدت را انجام بده.")
    s.setdefault("maintenance_message", "🛠 ربات موقتاً در حال بروزرسانی است.")
    s.setdefault("low_stock_threshold", 3)
    s.setdefault("notification_retention_days", 30)
    s.setdefault("max_ticket_open", 3)
    s.setdefault("broadcast_delay", 0.05)
    s.setdefault("purchase_confirmation", True)
    s.setdefault("show_stock_to_users", True)
    for u in data.get("users", {}).values():
        u.setdefault("notification_ids", [])
        u.setdefault("favorites", [])
        u.setdefault("applied_discount", None)
        u.setdefault("last_purchase_at", None)
        u.setdefault("last_order_id", None)
        u.setdefault("wallet_topup_ids", [])
        u.setdefault("rewarded_referral", False)
        u.setdefault("test_claim_count", 0)
        u.setdefault("test_extra_credits", 0)
        u.setdefault("test_history_ids", [])
    for s in data.get("services", {}).values():
        s.setdefault("category", "عمومی")
        s.setdefault("priority", 0)
        s.setdefault("badge", "")
        s.setdefault("min_stock_alert_sent", False)
    normalize_test_data()


def user_record(user_id):
    return data.get("users", {}).get(str(user_id))


def add_audit(admin_id, action, target="", details=""):
    try:
        rec = {
            "id": uid("audit"),
            "admin_id": int(admin_id),
            "action": action,
            "target": str(target),
            "details": str(details),
            "created_at": now_iso(),
        }
        data.setdefault("audit_logs", {})[rec["id"]] = rec
        if len(data["audit_logs"]) > 5000:
            old = sorted(data["audit_logs"].values(), key=lambda x: x.get("created_at", ""))[:500]
            for x in old:
                data["audit_logs"].pop(x["id"], None)
    except Exception:
        logger.exception("audit log failed")


async def notify_user(context, user_id, title, text, kind="info"):
    u = user_record(user_id)
    if not u:
        return
    nid = uid("ntf")
    data.setdefault("notifications", {})[nid] = {
        "id": nid, "user_id": int(user_id), "title": title,
        "text": text, "kind": kind, "read": False, "created_at": now_iso(),
    }
    u.setdefault("notification_ids", []).append(nid)
    try:
        await context.bot.send_message(chat_id=int(user_id), text=f"{title}\n\n{text}")
    except Exception:
        pass


def unread_notifications(user_id):
    return sum(1 for n in data.get("notifications", {}).values()
               if n.get("user_id") == user_id and not n.get("read"))


def cleanup_notifications():
    days = int(data.get("settings", {}).get("notification_retention_days", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for nid, n in list(data.get("notifications", {}).items()):
        dt=parse_dt(n.get("created_at"))
        if dt and dt < cutoff:
            data["notifications"].pop(nid, None)


def available_shop_services():
    out=[]
    for s in data.get("services", {}).values():
        if not s.get("active", True):
            continue
        if free_config_count(s) <= 0:
            continue
        out.append(s)
    return sorted(out, key=lambda x: (-int(x.get("priority",0)), x.get("name","")))


def calc_discount(user, plan):
    code = user.get("applied_discount") if user else None
    if not code:
        return int(plan.get("price",0)), None, 0
    d = data.get("discounts", {}).get(str(code).upper())
    if not d or not d.get("active"):
        return int(plan.get("price",0)), None, 0
    exp=parse_dt(d.get("expires_at"))
    if exp and datetime.now(timezone.utc) > exp:
        return int(plan.get("price",0)), None, 0
    max_uses=int(d.get("max_uses",0)); used=int(d.get("used",0))
    if max_uses>0 and used>=max_uses:
        return int(plan.get("price",0)), None, 0
    price=int(plan.get("price",0)); typ=d.get("type","percent"); val=int(d.get("value",0))
    if typ == "fixed":
        discount=min(price,max(0,val))
    else:
        discount=min(price,max(0,price*val//100))
    return price-discount, d, discount


def user_keyboard():
    unread=unread_notifications(getattr(user_keyboard, "current_user_id", 0)) if getattr(user_keyboard, "current_user_id", 0) else 0
    label = f"🔔 اعلان‌ها ({unread})" if unread else "🔔 اعلان‌ها"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 خرید سرویس", callback_data="shop"), InlineKeyboardButton("🧪 تست رایگان", callback_data="test_menu")],
        [InlineKeyboardButton("📦 سرویس‌های من", callback_data="my_services"), InlineKeyboardButton("👤 حساب کاربری", callback_data="account")],
        [InlineKeyboardButton("💳 کیف پول", callback_data="wallet"), InlineKeyboardButton("🧾 سفارش‌ها", callback_data="orders")],
        [InlineKeyboardButton(label, callback_data="notifications"), InlineKeyboardButton("🎁 کد تخفیف", callback_data="discount")],
        [InlineKeyboardButton("🎁 کد تخفیف", callback_data="discount"), InlineKeyboardButton("⭐ دعوت دوستان", callback_data="referral")],
        [InlineKeyboardButton("🎫 پشتیبانی", callback_data="support"), InlineKeyboardButton("📢 کانال", callback_data="channel")],
    ])


def admin_keyboard(role="owner"):
    rows = [
        [InlineKeyboardButton("📊 داشبورد", callback_data="admin_dashboard"), InlineKeyboardButton("👥 کاربران", callback_data="admin_users")],
        [InlineKeyboardButton("📦 سرویس‌ها", callback_data="admin_services"), InlineKeyboardButton("🔑 مرکز کانفیگ", callback_data="admin_configs")],
        [InlineKeyboardButton("⚡ مرکز عملیات", callback_data="admin_ops"), InlineKeyboardButton("💰 درخواست شارژ", callback_data="admin_topups")],
        [InlineKeyboardButton("🛒 سفارش‌ها", callback_data="admin_orders"), InlineKeyboardButton("💳 تراکنش‌ها", callback_data="admin_transactions")],
        [InlineKeyboardButton("🎁 تخفیف‌ها", callback_data="admin_discounts"), InlineKeyboardButton("⭐ دعوت‌ها", callback_data="admin_referral")],
        [InlineKeyboardButton("🎫 پشتیبانی", callback_data="admin_support"), InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📈 گزارش‌های حرفه‌ای", callback_data="admin_reports_pro"), InlineKeyboardButton("💾 بکاپ / بازیابی", callback_data="admin_backup")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings"), InlineKeyboardButton("🛡️ دسترسی مدیران", callback_data="admin_staff")] if role == "owner" else [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
    ]
    if role == "owner":
        rows.append([InlineKeyboardButton("🧾 لاگ مدیریت", callback_data="admin_audit")])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    existed = str(update.effective_user.id) in data.get("users", {})
    user = await ensure_user(update.effective_user)
    user_keyboard.current_user_id = update.effective_user.id

    # Referral capture only on first registration; self-referral is blocked.
    payload = ""
    try:
        raw = (update.message.text or "").strip()
        if raw.startswith("/start") and len(raw.split(maxsplit=1)) == 2:
            payload = raw.split(maxsplit=1)[1].strip()
    except Exception:
        payload = ""
    if (not existed) and payload.startswith("ref_"):
        code = payload[4:].strip().upper()
        referrer = next((x for x in data.get("users", {}).values() if x.get("referral_code", "").upper() == code), None)
        if referrer and int(referrer.get("id")) != int(update.effective_user.id):
            user["referred_by"] = int(referrer["id"])
            rid = uid("ref")
            data.setdefault("referrals", {})[rid] = {
                "id": rid, "referrer_id": int(referrer["id"]),
                "referred_user_id": int(update.effective_user.id),
                "created_at": now_iso(), "rewarded": False,
            }
            await save_data()

    if user.get("blocked") and not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی شما به این ربات مسدود شده است.")
        return
    if data.get("settings", {}).get("maintenance") and not get_admin_role(update.effective_user.id):
        await update.message.reply_text(data["settings"].get("maintenance_message", "🛠 ربات موقتاً در حال بروزرسانی است."))
        return
    await update.message.reply_text(
        f"🚀 سلام {esc(user.get('first_name') or '')}!\n\nبه {BRAND} خوش اومدی 💙\n\n"
        "پنل سرویس‌ها، کیف پول و پشتیبانی از منوی زیر در دسترسه:",
        reply_markup=user_keyboard(),
    )


async def show_wallet(query, user_id):
    u=user_record(user_id)
    pending=sum(1 for x in data.get("topups",{}).values() if x.get("user_id")==user_id and x.get("status")=="pending")
    support=PUBLIC_OWNER_USERNAME
    rows=[[InlineKeyboardButton("➕ افزایش موجودی", callback_data="wallet_topup")],
          [InlineKeyboardButton("🧾 تاریخچه تراکنش‌ها", callback_data="transactions")]]
    if pending:
        rows.append([InlineKeyboardButton(f"⏳ درخواست‌های در انتظار ({pending})", callback_data="topup_mystatus")])
    rows.append([InlineKeyboardButton("👤 ارتباط با مدیریت", url=PUBLIC_OWNER_URL)])
    rows.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="home")])
    await query.edit_message_text(
        f"💳 کیف پول\n\n💰 موجودی فعلی: {money(u.get('balance',0))} تومان\n\n"
        "🚧 شارژ آنلاین فعلاً فعال نیست.\n"
        "در نسخه بعدی اتصال مستقیم به درگاه پرداخت اضافه خواهد شد.\n\n"
        f"📞 برای افزایش موجودی فعلاً با مدیریت هماهنگ کن:\n@{support}\n\n"
        "✅ درخواستت از داخل ربات ثبت می‌شود و بعد از تأیید مدیریت، موجودی اضافه خواهد شد.",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def show_notifications(query, user_id):
    items=[n for n in data.get("notifications",{}).values() if n.get("user_id")==user_id]
    items=sorted(items,key=lambda x:x.get("created_at",""),reverse=True)[:20]
    for n in items:
        n["read"]=True
    if not items:
        text="🔔 اعلان‌ها\n\nاعلان جدیدی نداری."
    else:
        chunks=[]
        for n in items:
            chunks.append(f"• {esc(n.get('title','اعلان'))}\n{esc(n.get('text',''))}\n🕒 {esc(n.get('created_at',''))}")
        text="🔔 اعلان‌ها\n\n"+"\n\n".join(chunks)
    await save_data()
    user_keyboard.current_user_id=user_id
    await query.edit_message_text(text, reply_markup=back_home(), parse_mode="HTML")


async def show_orders(query,user_id):
    u=user_record(user_id)
    ids=u.get("order_ids",[]) if u else []
    orders=[data["orders"].get(i) for i in ids if data["orders"].get(i)]
    orders=sorted(orders,key=lambda x:x.get("created_at",""),reverse=True)[:30]
    if not orders:
        await query.edit_message_text("🧾 هنوز سفارشی ثبت نشده است.",reply_markup=back_home())
        return
    rows=[]
    for o in orders:
        status={"paid":"✅ پرداخت‌شده","pending":"⏳ در انتظار","failed":"❌ ناموفق","refunded":"↩️ برگشت"}.get(o.get("status"),o.get("status"))
        rows.append([InlineKeyboardButton(f"{status} | {money(o.get('amount',0))} تومان",callback_data=f"order_view:{o['id']}")])
    rows.append([InlineKeyboardButton("🔙 منوی اصلی",callback_data="home")])
    await query.edit_message_text("🧾 تاریخچه سفارش‌ها\n\nسفارش را انتخاب کن:",reply_markup=InlineKeyboardMarkup(rows))


async def show_order_detail(query, order_id):
    o=data.get("orders",{}).get(order_id)
    if not o or o.get("user_id")!=query.from_user.id:
        await query.answer("دسترسی ندارید.",show_alert=True); return
    await query.edit_message_text(
        f"🧾 سفارش {esc(order_id)}\n\n📦 پلن: {esc(data.get('services',{}).get(o.get('service_id'),{}).get('name','-'))}\n"
        f"💰 مبلغ: {money(o.get('amount',0))} تومان\n📌 وضعیت: {esc(o.get('status','-'))}\n"
        f"💳 پرداخت: {esc(o.get('payment_method','-'))}\n🕒 زمان: {esc(o.get('created_at','-'))}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 سفارش‌ها",callback_data="orders")],[InlineKeyboardButton("🏠 خانه",callback_data="home")]])
    )


async def wallet_topup_start(query, context):
    context.user_data["state"]="wallet_topup_amount"
    await query.edit_message_text(
        "➕ درخواست افزایش موجودی\n\nمبلغ موردنظر را به تومان بفرست.\n"
        f"👤 مدیریت: @{PUBLIC_OWNER_USERNAME}\n"
        "درخواست پس از ثبت برای مدیریت ارسال می‌شود.\n\n/cancel برای لغو",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 ارتباط با مدیریت", url=PUBLIC_OWNER_URL)], [InlineKeyboardButton("🔙 خانه", callback_data="home")]]))


async def topup_mystatus(query,user_id):
    reqs=[x for x in data.get("topups",{}).values() if x.get("user_id")==user_id]
    reqs=sorted(reqs,key=lambda x:x.get("created_at",""),reverse=True)[:10]
    if not reqs:
        text="💰 هیچ درخواست شارژی ثبت نشده است."
    else:
        labels={"pending":"⏳ در انتظار","approved":"✅ تایید","rejected":"❌ رد شده"}
        text="💰 درخواست‌های شارژ\n\n"+"\n".join(f"• {money(x.get('amount',0))} تومان | {labels.get(x.get('status'),x.get('status'))} | {x.get('created_at','')}" for x in reqs)
    await query.edit_message_text(text,reply_markup=back_home())


async def admin_topups(query):
    if not can_manage_finance(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
    reqs=sorted(data.get("topups",{}).values(),key=lambda x:x.get("created_at",""),reverse=True)
    pending=[x for x in reqs if x.get("status")=="pending"]
    total=sum(int(x.get("amount",0)) for x in reqs if x.get("status")=="approved")
    rows=[[InlineKeyboardButton(f"⏳ در انتظار: {len(pending)}",callback_data="admin_topups_pending")]]
    for r in pending[:20]:
        rows.append([InlineKeyboardButton(f"👤 {r.get('user_id')} | {money(r.get('amount',0))} تومان",callback_data=f"topup_view:{r['id']}")])
    rows.append([InlineKeyboardButton("🔙 پنل مدیریت",callback_data="admin_home")])
    await query.edit_message_text(f"💰 مدیریت درخواست‌های شارژ\n\n✅ مجموع تاییدشده: {money(total)} تومان\n📥 کل در انتظار: {len(pending)}",reply_markup=InlineKeyboardMarkup(rows))


async def admin_topup_view(query,topup_id):
    if not can_manage_finance(query.from_user.id):
        await query.answer("دسترسی ندارید.",show_alert=True); return
    r=data.get("topups",{}).get(topup_id)
    if not r:
        await query.edit_message_text("❌ درخواست پیدا نشد.",reply_markup=back_admin()); return
    status=r.get("status")
    buttons=[]
    if status=="pending":
        buttons=[[InlineKeyboardButton("✅ تایید و شارژ",callback_data=f"topup_approve:{topup_id}")],[InlineKeyboardButton("❌ رد درخواست",callback_data=f"topup_reject:{topup_id}")]]
    buttons.append([InlineKeyboardButton("🔙 درخواست‌ها",callback_data="admin_topups")])
    await query.edit_message_text(
        f"💰 درخواست شارژ\n\n🆔 درخواست: {esc(topup_id)}\n👤 کاربر: <code>{r.get('user_id')}</code>\n"
        f"💵 مبلغ: {money(r.get('amount',0))} تومان\n📌 وضعیت: {esc(status or '-') }\n🕒 زمان: {esc(r.get('created_at','-'))}",
        reply_markup=InlineKeyboardMarkup(buttons),parse_mode="HTML")


async def approve_topup(query, context, topup_id, approved):
    if not can_manage_finance(query.from_user.id):
        await query.answer("دسترسی ندارید.",show_alert=True); return
    r=data.get("topups",{}).get(topup_id)
    if not r or r.get("status")!="pending":
        await query.answer("این درخواست قبلاً پردازش شده.",show_alert=True); return
    r["status"]="approved" if approved else "rejected"
    r["processed_at"]=now_iso(); r["processed_by"]=query.from_user.id
    user=user_record(r.get("user_id"))
    if approved and user:
        amount=max(0,int(r.get("amount",0)))
        user["balance"]=int(user.get("balance",0))+amount
        txid=uid("tx")
        data["transactions"][txid]={"id":txid,"user_id":int(r["user_id"]),"type":"wallet_topup","amount":amount,"description":"افزایش موجودی تاییدشده توسط مدیریت","created_at":now_iso(),"reference":topup_id}
        user.setdefault("transaction_ids",[]).append(txid)
        await notify_user(context,r["user_id"],"✅ افزایش موجودی تایید شد",f"مبلغ {money(amount)} تومان به کیف پول اضافه شد.","wallet")
    else:
        await notify_user(context,r["user_id"],"❌ درخواست افزایش موجودی رد شد","درخواست شارژ شما توسط مدیریت رد شد. برای پیگیری با مدیریت ارتباط بگیر.","wallet")
    add_audit(query.from_user.id,"topup_approved" if approved else "topup_rejected",topup_id)
    await save_data()
    await query.answer("انجام شد ✅")
    await admin_topups(query)


async def show_shop(query):
    services=available_shop_services()
    if not services:
        await query.edit_message_text(
            f"{data.get('settings',{}).get('shop_title','🛒 فروشگاه')}\n\n"
            "😔 فعلاً هیچ پلن آماده فروشی وجود ندارد.\n\n"
            "موجودی فروش به‌صورت زنده بررسی می‌شود؛ وقتی کانفیگ شارژ شود، پلن دوباره در فروشگاه ظاهر خواهد شد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 بروزرسانی",callback_data="shop")],[InlineKeyboardButton("🏠 منوی اصلی",callback_data="home")]])
        ); return
    groups={}
    for s in services: groups.setdefault(s.get("category","عمومی"),[]).append(s)
    rows=[]
    for cat,items in groups.items():
        rows.append([InlineKeyboardButton(f"📂 {cat}",callback_data=f"shop_category:{cat[:35]}")])
        for s in items[:30]:
            stock=free_config_count(s)
            badge=(s.get("badge") or "").strip()
            label=f"🟢 {badge+' ' if badge else ''}{s.get('name','پلن')} • {money(s.get('price',0))} تومان"
            if data.get("settings",{}).get("show_stock_to_users",True): label += f" • {stock} موجود"
            rows.append([InlineKeyboardButton(label,callback_data=f"view_service:{s['id']}")])
    rows.append([InlineKeyboardButton("🔄 بروزرسانی",callback_data="shop"),InlineKeyboardButton("🏠 خانه",callback_data="home")])
    await query.edit_message_text(
        f"{data.get('settings',{}).get('shop_title','🛒 فروشگاه NovaLinkVPN')}\n\n"
        f"{esc(data.get('settings',{}).get('shop_description',''))}\n\n✅ فقط پلن‌های قابل خرید نمایش داده می‌شوند.\n"
        "🔄 موجودی هر پلن لحظه‌ای بررسی می‌شود.",
        reply_markup=InlineKeyboardMarkup(rows),parse_mode="HTML")


async def shop_category(query,category):
    items=[s for s in available_shop_services() if s.get("category","عمومی")[:35]==category]
    if not items:
        await query.edit_message_text("❌ در این دسته پلن موجود نیست.",reply_markup=back_home()); return
    rows=[]
    for s in items:
        stock=free_config_count(s)
        rows.append([InlineKeyboardButton(f"🟢 {s.get('name')} • {money(s.get('price',0))} تومان • {stock}",callback_data=f"view_service:{s['id']}")])
    rows.append([InlineKeyboardButton("🔙 فروشگاه",callback_data="shop")])
    await query.edit_message_text(f"📂 {esc(category)}\n\nپلن موردنظر را انتخاب کن:",reply_markup=InlineKeyboardMarkup(rows))


async def view_service(query, service_id):
    service=data.get("services",{}).get(service_id)
    if not service or not service.get("active",True) or free_config_count(service)<=0:
        await query.edit_message_text("❌ این پلن در حال حاضر موجود نیست.",reply_markup=back_home()); return
    user=user_record(query.from_user.id); net,d,disc=calc_discount(user,service)
    stock=free_config_count(service)
    text=(f"📦 {esc(service.get('name',''))}\n\n📂 دسته: {esc(service.get('category','عمومی'))}\n"
          f"💾 حجم: {service.get('traffic_gb',0)} GB\n🌍 سرور: {esc(service.get('server','-'))}\n"
          f"⏳ مدت: {service.get('duration_days',0)} روز\n💰 قیمت اصلی: {money(service.get('price',0))} تومان\n")
    if d:
        text += f"🎁 تخفیف {esc(d.get('code',''))}: -{money(disc)} تومان\n💵 قیمت نهایی: {money(net)} تومان\n"
    if data.get("settings",{}).get("show_stock_to_users",True): text+=f"📦 موجودی: {stock} عدد\n"
    text += f"\nℹ️ {esc(service.get('description',''))}\n\n⚡ قبل از پرداخت، اطلاعات سفارش را یک‌بار بررسی کن."
    await query.edit_message_text(text,reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ادامه و پرداخت",callback_data=f"confirm_buy:{service_id}")],
        [InlineKeyboardButton("❤️ افزودن به علاقه‌مندی",callback_data=f"favorite:{service_id}")],
        [InlineKeyboardButton("🔙 فروشگاه",callback_data="shop")]
    ]),parse_mode="HTML")


async def confirm_buy(query, service_id):
    service=data.get("services",{}).get(service_id); user=user_record(query.from_user.id)
    if not service or not user or not service.get("active",True) or free_config_count(service)<=0:
        await query.edit_message_text("❌ این پلن دیگر موجود نیست.",reply_markup=back_home()); return
    net,d,disc=calc_discount(user,service)
    await query.edit_message_text(
        f"🧾 تایید نهایی خرید\n\n📦 {esc(service.get('name',''))}\n💰 مبلغ قابل پرداخت: {money(net)} تومان\n"
        f"💳 موجودی کیف پول: {money(user.get('balance',0))} تومان\n📦 موجودی کانفیگ: {free_config_count(service)}\n\n"
        "با تایید، مبلغ از کیف پول کسر و یک کانفیگ اختصاصی تحویل داده می‌شود.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 تایید خرید",callback_data=f"buy_wallet:{service_id}")],[InlineKeyboardButton("❌ لغو",callback_data=f"view_service:{service_id}")]]))


async def _buy_with_wallet_locked(query,user_id,service_id):
    user=user_record(user_id); plan=data.get("services",{}).get(service_id)
    if not user or not plan or not plan.get("active",True):
        await query.edit_message_text("❌ سرویس قابل خرید نیست.",reply_markup=back_home()); return
    if free_config_count(plan)<=0:
        await query.edit_message_text("❌ موجودی کانفیگ این پلن تمام شده است.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 فروشگاه",callback_data="shop")],[InlineKeyboardButton("🏠 خانه",callback_data="home")]])); return
    final_price,discount,discount_amount=calc_discount(user,plan)
    if user.get("balance",0)<final_price:
        await query.edit_message_text(f"❌ موجودی کافی نیست.\n\n💰 قیمت نهایی: {money(final_price)} تومان\n💳 موجودی: {money(user.get('balance',0))} تومان",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 کیف پول",callback_data="wallet")],[InlineKeyboardButton("🛒 فروشگاه",callback_data="shop")]])); return
    owned_id=uid("svc"); order_id=uid("ord"); config_item=None
    normalize_service_inventory(plan)
    for item in plan["config_pool"]:
        if item.get("status")=="free" and item.get("config"):
            config_item=item; break
    if not config_item:
        await query.edit_message_text("❌ کانفیگ قابل تخصیص پیدا نشد.",reply_markup=back_home()); return
    # Atomic-ish JSON operation: reserve config first, then charge. Rollback on failure.
    before_balance=int(user.get("balance",0)); before_status=config_item.get("status")
    try:
        config_item["status"]="assigned"; config_item["assigned_to"]=user_id; config_item["assigned_service_id"]=owned_id; config_item["assigned_at"]=now_iso()
        user["balance"]=before_balance-final_price
        expires=datetime.now(timezone.utc)+timedelta(days=int(plan.get("duration_days",30)))
        owned={"id":owned_id,"user_id":user_id,"plan_id":service_id,"plan_name":plan.get("name",""),"traffic_gb":plan.get("traffic_gb",0),"server":plan.get("server",""),"config":config_item.get("config"),"config_item_id":config_item.get("id"),"created_at":now_iso(),"expires_at":expires.isoformat(),"status":"active","traffic_used_gb":0,"category":plan.get("category","عمومی")}
        order={"id":order_id,"user_id":user_id,"service_id":service_id,"amount":final_price,"original_amount":int(plan.get("price",0)),"discount_code":discount.get("code") if discount else None,"discount_amount":discount_amount,"status":"paid","created_at":now_iso(),"payment_method":"wallet"}
        txid=uid("tx")
        tx={"id":txid,"user_id":user_id,"type":"purchase","amount":-final_price,"description":f"خرید {plan.get('name','')}","created_at":now_iso(),"reference":order_id}
        data["services"][owned_id]=owned; data["orders"][order_id]=order; data["transactions"][txid]=tx
        user.setdefault("service_ids",[]).append(owned_id); user.setdefault("order_ids",[]).append(order_id); user.setdefault("transaction_ids",[]).append(txid)
        user["last_purchase_at"]=now_iso(); user["last_order_id"]=order_id
        if discount:
            discount["used"]=int(discount.get("used",0))+1; user["applied_discount"]=None
        data["stats"]["total_sales"]=int(data["stats"].get("total_sales",0))+1
        data["stats"]["total_revenue"]=int(data["stats"].get("total_revenue",0))+final_price

        # Reward the referrer exactly once, after the referred user's first paid order.
        if not user.get("rewarded_referral") and user.get("referred_by"):
            referrer=data.get("users",{}).get(str(user.get("referred_by")))
            reward=int(data.get("settings",{}).get("referral_reward",0))
            if referrer and reward>0:
                referrer["balance"]=int(referrer.get("balance",0))+reward
                referrer.setdefault("points",0)
                txr=uid("tx")
                data["transactions"][txr]={"id":txr,"user_id":int(referrer["id"]),"type":"referral_reward","amount":reward,"description":"پاداش اولین خرید دعوت‌شده","created_at":now_iso(),"reference":order_id}
                referrer.setdefault("transaction_ids",[]).append(txr)
                user["rewarded_referral"]=True
                for rr in data.get("referrals",{}).values():
                    if rr.get("referred_user_id")==user_id and not rr.get("rewarded"):
                        rr["rewarded"]=True; rr["rewarded_at"]=now_iso(); rr["reward"] = reward
                        break
        await save_data()
    except Exception:
        user["balance"]=before_balance
        config_item["status"]=before_status
        config_item["assigned_to"]=None
        config_item["assigned_service_id"]=None
        config_item["assigned_at"]=None
        data["services"].pop(owned_id,None)
        data["orders"].pop(order_id,None)
        # Remove any partially appended references/transaction created before failure.
        for key in ("service_ids", "order_ids", "transaction_ids"):
            if key in user:
                user[key] = [x for x in user.get(key, []) if x not in {owned_id, order_id, txid if 'txid' in locals() else ''}]
        if 'txid' in locals():
            data.get("transactions",{}).pop(txid,None)
        await save_data()
        await query.edit_message_text("❌ خطای داخلی در ثبت خرید؛ مبلغ محفوظ ماند و کانفیگ برگشت داده شد.",reply_markup=back_home()); return
    user_keyboard.current_user_id=user_id
    await query.edit_message_text(
        f"✅ خرید با موفقیت انجام شد!\n\n📦 سرویس: {esc(plan.get('name',''))}\n💰 مبلغ: {money(final_price)} تومان\n"
        f"⏳ انقضا: {expires.strftime('%Y-%m-%d')}\n🧾 سفارش: <code>{esc(order_id)}</code>\n\n🔑 کانفیگ اختصاصی تو:\n\n<code>{esc(config_item.get('config',''))}</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 سرویس‌های من",callback_data="my_services")],[InlineKeyboardButton("🧾 جزئیات سفارش",callback_data=f"order_view:{order_id}")],[InlineKeyboardButton("🏠 خانه",callback_data="home")]]),parse_mode="HTML")


async def apply_discount(update, code):
    user=user_record(update.effective_user.id); d=data.get("discounts",{}).get(code.upper())
    if not user or not d or not d.get("active"):
        await update.message.reply_text("❌ کد تخفیف معتبر نیست.",reply_markup=user_keyboard()); return
    exp=parse_dt(d.get("expires_at"))
    if exp and datetime.now(timezone.utc)>exp:
        d["active"]=False; await save_data(); await update.message.reply_text("❌ تاریخ این کد گذشته است.",reply_markup=user_keyboard()); return
    max_uses=int(d.get("max_uses",0)); used=int(d.get("used",0))
    if max_uses>0 and used>=max_uses:
        await update.message.reply_text("❌ ظرفیت استفاده از این کد تمام شده است.",reply_markup=user_keyboard()); return
    user["applied_discount"]=d.get("code",code.upper())
    await save_data()
    await update.message.reply_text(f"✅ کد {esc(user['applied_discount'])} آماده استفاده شد.\nدر خرید بعدی تخفیف محاسبه می‌شود.",reply_markup=user_keyboard(),parse_mode="HTML")


async def favorite_toggle(query, service_id):
    u=user_record(query.from_user.id); s=data.get("services",{}).get(service_id)
    if not u or not s:
        await query.answer("پلن پیدا نشد.",show_alert=True); return
    fav=u.setdefault("favorites",[])
    if service_id in fav:
        fav.remove(service_id); msg="از علاقه‌مندی حذف شد."
    else:
        fav.append(service_id); msg="به علاقه‌مندی اضافه شد."
    await save_data(); await query.answer(msg,show_alert=True)


async def show_account(query,user_id):
    u=user_record(user_id)
    active=sum(1 for sid in u.get("service_ids",[]) if data.get("services",{}).get(sid,{}).get("status")=="active")
    fav=len(u.get("favorites",[])); unread=unread_notifications(user_id)
    await query.edit_message_text(
        f"👤 حساب کاربری\n\n🆔 ID: <code>{user_id}</code>\n"
        f"👤 نام: {esc(u.get('first_name') or '-') }\n📦 سرویس‌های فعال: {active}\n💳 موجودی: {money(u.get('balance',0))} تومان\n"
        f"⭐ امتیاز: {u.get('points',0)}\n❤️ علاقه‌مندی‌ها: {fav}\n🔔 اعلان خوانده‌نشده: {unread}\n{test_status_for_account(user_id)}\n🗓 عضویت: {esc(u.get('created_at','-'))}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧾 سفارش‌ها",callback_data="orders"),InlineKeyboardButton("🔔 اعلان‌ها",callback_data="notifications")],[InlineKeyboardButton("🔙 خانه",callback_data="home")]]),parse_mode="HTML")



async def show_owned_service(query, service_id):
    service=data.get("services",{}).get(service_id)
    if not service or service.get("user_id") != query.from_user.id:
        await query.edit_message_text("❌ این سرویس متعلق به حساب شما نیست.",reply_markup=back_home()); return
    expires=parse_dt(service.get("expires_at"))
    now=datetime.now(timezone.utc)
    if expires and expires <= now and service.get("status") != "expired":
        service["status"]="expired"
        await save_data()
    status="🟢 فعال" if service.get("status")=="active" and (not expires or expires>now) else "🔴 منقضی"
    remaining="-"
    if expires:
        sec=max(0,int((expires-now).total_seconds())); days=sec//86400; hours=(sec%86400)//3600
        remaining=f"{days} روز و {hours} ساعت"
    await query.edit_message_text(
        f"📦 {esc(service.get('plan_name','سرویس'))}\n\n"
        f"📡 وضعیت: {status}\n💾 حجم: {service.get('traffic_gb',0)} GB\n"
        f"📊 مصرف ثبت‌شده: {service.get('traffic_used_gb',0)} GB\n🌍 سرور: {esc(service.get('server','-'))}\n"
        f"⏳ انقضا: {expires.strftime('%Y-%m-%d %H:%M') if expires else '-'}\n⏱ باقی‌مانده: {remaining}\n\n"
        f"🔑 کانفیگ اختصاصی:\n<code>{esc(service.get('config') or 'ثبت نشده')}</code>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تمدید",callback_data=f"renew:{service_id}"),InlineKeyboardButton("📋 دریافت کانفیگ",callback_data=f"send_config:{service_id}")],
            [InlineKeyboardButton("🧾 سفارش مرتبط",callback_data=f"orders")],
            [InlineKeyboardButton("📦 سرویس‌های من",callback_data="my_services")]
        ]),parse_mode="HTML")


async def admin_reports_pro(query):
    if not can_admin(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
    now=datetime.now(timezone.utc); day=now-timedelta(days=1); week=now-timedelta(days=7); month=now-timedelta(days=30)
    orders=list(data.get("orders",{}).values()); users=list(data.get("users",{}).values()); txs=list(data.get("transactions",{}).values())
    paid=[o for o in orders if o.get("status")=="paid"]
    def period(p):
        dt=parse_dt(p.get("created_at")); return dt
    rev=lambda start: sum(int(o.get("amount",0)) for o in paid if (period(o) and period(o)>=start))
    sales=lambda start: sum(1 for o in paid if (period(o) and period(o)>=start))
    active_services=sum(1 for s in data.get("services",{}).values() if s.get("user_id") and s.get("status")=="active")
    stocks=[]
    for s in data.get("services",{}).values():
        if "config_pool" in s and s.get("name"):
            stocks.append((s.get("name"),free_config_count(s)))
    low=[f"• {esc(n)}: {c}" for n,c in stocks if c<=int(data.get("settings",{}).get("low_stock_threshold",3))]
    text=("📈 گزارش حرفه‌ای NovaLinkVPN\n\n"
          f"👥 کاربران: {len(users)}\n🧑‍💻 کاربران فعال 24h: {sum(1 for u in users if (parse_dt(u.get('last_seen')) or now)<now and (parse_dt(u.get('last_seen')) or now)>=day)}\n"
          f"📦 سرویس‌های فعال کاربری: {active_services}\n\n"
          f"💰 فروش 24h: {money(rev(day))} تومان | {sales(day)} سفارش\n"
          f"💰 فروش 7d: {money(rev(week))} تومان | {sales(week)} سفارش\n"
          f"💰 فروش 30d: {money(rev(month))} تومان | {sales(month)} سفارش\n\n"
          f"🎫 تیکت باز: {sum(1 for t in data.get('tickets',{}).values() if t.get('status')=='open')}\n"
          f"💰 درخواست شارژ در انتظار: {sum(1 for x in data.get('topups',{}).values() if x.get('status')=='pending')}\n\n"
          "⚠️ موجودی پایین:\n"+("\n".join(low) if low else "✅ موردی نیست"))
    await query.edit_message_text(text,reply_markup=back_admin(),parse_mode="HTML")


async def admin_audit(query):
    if not is_owner(query.from_user.id):
        await query.edit_message_text("⛔ فقط Owner.",reply_markup=back_admin()); return
    logs=sorted(data.get("audit_logs",{}).values(),key=lambda x:x.get("created_at",""),reverse=True)[:30]
    if not logs: text="🧾 هنوز لاگ مدیریتی ثبت نشده است."
    else: text="🧾 لاگ مدیریت\n\n"+"\n".join(f"• {x.get('action')} | {x.get('admin_id')} | {x.get('target')} | {x.get('created_at')}" for x in logs)
    await query.edit_message_text(text,reply_markup=back_admin())


async def admin_configs_home(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
    total=free=sold=0
    low=[]
    for s in data.get("services",{}).values():
        if "config_pool" not in s: continue
        f=free_config_count(s); t=total_config_count(s); total+=t; free+=f; sold+=t-f
        if f<=int(data.get("settings",{}).get("low_stock_threshold",3)): low.append(f"• {s.get('name','-')}: {f}")
    await query.edit_message_text(
        "🔑 مرکز مدیریت کانفیگ\n\n"
        f"📦 کل کانفیگ‌ها: {total}\n🟢 آماده فروش: {free}\n🔴 فروخته/اختصاص‌یافته: {sold}\n⚠️ کم‌موجودی: {len(low)}\n\n"
        "کانفیگ‌های آماده فروش را از کانفیگ‌های تحویل‌شده جدا نگه دار.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 کانفیگ‌های آماده فروش",callback_data="config_free" )],
            [InlineKeyboardButton("🔴 کانفیگ‌های فروخته‌شده",callback_data="config_sold")],
            [InlineKeyboardButton("🔎 جستجوی کانفیگ",callback_data="config_search")],
            [InlineKeyboardButton("🔄 بروزرسانی موجودی",callback_data="admin_configs")],
            [InlineKeyboardButton("🔙 پنل مدیریت",callback_data="admin_home")]
        ]))


async def admin_configs_free(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
    lines=[]
    for s in data.get("services",{}).values():
        free_items=[x for x in s.get("config_pool",[]) if x.get("status")=="free"]
        if not free_items: continue
        lines.append(f"📦 {s.get('name','-')} | 🟢 {len(free_items)}")
        for x in free_items[:8]: lines.append(f"  • {x.get('id')} | {mask_config(x.get('config',''))}")
    text="🟢 کانفیگ‌های آماده فروش\n\n"+("\n".join(lines) if lines else "هیچ کانفیگ آزادی موجود نیست.")
    await query.edit_message_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 مرکز کانفیگ",callback_data="admin_configs")],[InlineKeyboardButton("📋 بکاپ",callback_data="admin_backup")]]) )


async def admin_configs_sold(query):
    if not can_manage_services(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
    rows=[]
    for sid,s in data.get("services",{}).items():
        for x in s.get("config_pool",[]):
            if x.get("status")=="assigned":
                rows.append((x.get("assigned_at", ""),s,x))
    rows=sorted(rows,key=lambda z:z[0],reverse=True)[:60]
    if not rows: text="🔴 هنوز هیچ کانفیگی فروخته/اختصاص داده نشده است."
    else:
        chunks=[]
        for dt,s,x in rows:
            chunks.append(f"📦 {esc(s.get('name','-'))}\n👤 {x.get('assigned_to')}\n🔑 {x.get('id')} | {mask_config(x.get('config',''))}\n🕒 {dt}")
        text=f"🔴 کانفیگ‌های فروخته‌شده\n\n📦 تعداد نمایش داده‌شده: {len(chunks)}\n\n"+"\n\n".join(chunks)
    await query.edit_message_text(text,reply_markup=back_admin(),parse_mode="HTML")


async def config_search_start(query,context):
    context.user_data["state"]="config_search"
    await query.edit_message_text("🔎 ID کانفیگ، بخشی از خود کانفیگ یا Telegram ID خریدار را بفرست.",reply_markup=back_admin())


async def handle_config_search(update,text):
    q=text.strip().lower(); rows=[]
    for s in data.get("services",{}).values():
        for x in s.get("config_pool",[]):
            blob=" ".join([str(x.get("id","")),str(x.get("assigned_to","")),str(x.get("config",""))]).lower()
            if q in blob:
                rows.append(f"📦 {s.get('name','-')} | {x.get('id')} | {'🟢 آزاد' if x.get('status')=='free' else '🔴 اختصاص‌یافته'} | {mask_config(x.get('config',''))}")
    rows=rows[:50]
    context=update.message
    await update.message.reply_text("🔎 نتیجه جستجو\n\n"+("\n".join(rows) if rows else "❌ چیزی پیدا نشد."),reply_markup=back_admin())


async def add_topup_request(update,amount,context):
    uid_=update.effective_user.id; amount=int(amount)
    if amount<1000 or amount>1000000000:
        await update.message.reply_text("❌ مبلغ باید بین 1,000 تا 1,000,000,000 تومان باشد."); return
    existing=sum(1 for x in data.get("topups",{}).values() if x.get("user_id")==uid_ and x.get("status")=="pending")
    if existing>=3:
        await update.message.reply_text("⏳ چند درخواست در انتظار داری. لطفاً تا بررسی آنها صبر کن."); context.user_data.clear(); return
    tid=uid("topup")
    r={"id":tid,"user_id":uid_,"amount":amount,"status":"pending","created_at":now_iso()}
    data.setdefault("topups",{})[tid]=r
    user_record(uid_).setdefault("wallet_topup_ids",[]).append(tid)
    add_audit(uid_,"topup_requested",tid,f"amount={amount}") if is_owner(uid_) else None
    await save_data(); context.user_data.clear()
    if ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID,text=f"💰 درخواست شارژ جدید\n\n👤 کاربر: {uid_}\n💵 مبلغ: {money(amount)} تومان\n🆔 {tid}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔎 بررسی در پنل",callback_data=f"topup_view:{tid}")]]))
        except Exception: pass
    await update.message.reply_text(f"✅ درخواست شارژ ثبت شد.\n\n💵 مبلغ: {money(amount)} تومان\n🆔 درخواست: {tid}\n\nپس از تایید مدیریت، موجودی کیف پولت افزایش پیدا می‌کند.",reply_markup=user_keyboard())


async def admin_broadcast_menu(query,context):
    if not can_broadcast(query.from_user.id):
        await query.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
    await query.edit_message_text("📢 انتخاب گروه ارسال",reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 همه کاربران",callback_data="broadcast_target:all")],
        [InlineKeyboardButton("🟢 کاربران دارای سرویس فعال",callback_data="broadcast_target:active")],
        [InlineKeyboardButton("⏳ نزدیک انقضا",callback_data="broadcast_target:expiring")],
        [InlineKeyboardButton("🔙 پنل مدیریت",callback_data="admin_home")]
    ]))


async def broadcast_target_start(query,context,target):
    context.user_data["state"]=f"broadcast_text:{target}"
    await query.edit_message_text(f"📢 متن ارسال برای گروه «{target}» را بفرست.\n\n/cancel برای لغو",reply_markup=back_admin())


async def run_targeted_broadcast(update,text,context,target):
    now=datetime.now(timezone.utc); ids=[]
    for u in data.get("users",{}).values():
        if u.get("blocked"): continue
        if target=="all": ok=True
        elif target=="active": ok=any(data.get("services",{}).get(sid,{}).get("status")=="active" for sid in u.get("service_ids",[]))
        else:
            ok=False
            for sid in u.get("service_ids",[]):
                s=data.get("services",{}).get(sid,{})
                exp=parse_dt(s.get("expires_at"));
                if exp and now<=exp<=now+timedelta(days=7): ok=True; break
        if ok: ids.append(int(u["id"]))
    bid=uid("broadcast"); data.setdefault("broadcasts",{})[bid]={"id":bid,"admin_id":update.effective_user.id,"text":text,"target":target,"created_at":now_iso(),"sent":0,"failed":0}
    await save_data(); sent=failed=0
    for cid in ids:
        try:
            await context.bot.send_message(chat_id=cid,text=text); sent+=1
        except Exception: failed+=1
        await asyncio.sleep(float(data.get("settings",{}).get("broadcast_delay",0.05)))
    data["broadcasts"][bid]["sent"]=sent; data["broadcasts"][bid]["failed"]=failed
    add_audit(update.effective_user.id,"broadcast",bid,f"target={target},sent={sent},failed={failed}")
    await save_data()
    await update.message.reply_text(f"✅ ارسال تمام شد.\n\n📨 موفق: {sent}\n❌ ناموفق: {failed}",reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    u=await ensure_user(q.from_user); user_keyboard.current_user_id=q.from_user.id
    if u.get("blocked") and not is_owner(q.from_user.id):
        await q.edit_message_text("⛔ دسترسی شما مسدود است."); return
    k=q.data or ""
    if k=="wallet_topup": return await wallet_topup_start(q,context)
    if k=="topup_mystatus": return await topup_mystatus(q,q.from_user.id)
    if k=="notifications": return await show_notifications(q,q.from_user.id)
    if k=="orders": return await show_orders(q,q.from_user.id)
    if k.startswith("order_view:"): return await show_order_detail(q,k.split(":",1)[1])
    if k.startswith("favorite:"): return await favorite_toggle(q,k.split(":",1)[1])
    if k.startswith("shop_category:"): return await shop_category(q,k.split(":",1)[1])
    if k.startswith("confirm_buy:"): return await confirm_buy(q,k.split(":",1)[1])
    if k=="admin_topups": return await admin_topups(q)
    if k.startswith("topup_view:"): return await admin_topup_view(q,k.split(":",1)[1])
    if k.startswith("topup_approve:"): return await approve_topup(q,context,k.split(":",1)[1],True)
    if k.startswith("topup_reject:"): return await approve_topup(q,context,k.split(":",1)[1],False)
    if k=="admin_ops": return await admin_ops_center(q)
    if k=="admin_stock_alerts": return await admin_stock_alerts(q)
    if k=="admin_reports_pro": return await admin_reports_pro(q)
    if k=="admin_audit": return await admin_audit(q)
    if k=="config_search": return await config_search_start(q,context)
    # 🧪 FREE TEST USER ROUTES
    if k == "test_menu":
        return await user_test_page(q, q.from_user.id)
    if k == "claim_test":
        return await claim_test_button(q, q.from_user.id)
    if k == "test_config":
        return await send_test_config_message(q, q.from_user.id)

    if k == "admin_test":
        if not can_manage_services(q.from_user.id):
            await q.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin()); return
        return await admin_test_center(q)
    if k == "test_users":
        return await admin_test_users(q)
    if k.startswith("test_user_view:"):
        return await admin_test_user_view(q, k.split(":",1)[1])
    if k == "test_free_list":
        return await admin_test_free_list(q)
    if k == "test_sold_list":
        return await admin_test_sold_list(q)
    if k == "test_stats":
        return await admin_test_stats(q)
    if k == "test_settings":
        return await admin_test_settings(q)
    if k == "test_clear_free":
        return await admin_test_clear_free(q)
    if k == "test_grant":
        if not can_manage_users(q.from_user.id):
            await q.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin()); return
        context.user_data["state"] = "test_grant"
        await q.edit_message_text(
            "🎁 مجوز تست اضافه\n\n"
            "شناسه عددی کاربر یا username را بفرست.\n"
            "مثال: 123456789 یا @username\n\n"
            "بعد از پیدا شدن کاربر، تعداد تست اضافه را می‌گیریم.\n/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return
    if k.startswith("test_grant_user:"):
        if not can_manage_users(q.from_user.id):
            await q.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin()); return
        target_id = k.split(":",1)[1]
        target = data.get("users",{}).get(str(target_id))
        if not target:
            await q.edit_message_text("❌ کاربر پیدا نشد.", reply_markup=back_admin()); return
        context.user_data["state"] = f"test_grant_count:{target_id}"
        await q.edit_message_text(
            f"🎁 مجوز تست اضافه برای کاربر {target_id}\n\n"
            "تعداد مجوز را به صورت عدد بفرست.\nمثال: 1 یا 3",
            reply_markup=back_admin(),
        )
        return
    if k == "test_toggle":
        if not is_owner(q.from_user.id):
            await q.answer("فقط Owner.", show_alert=True); return
        normalize_test_data()
        _test_settings()["test_enabled"] = not _test_settings().get("test_enabled", True)
        await save_data()
        return await admin_test_settings(q)
    if k == "test_duration":
        if not is_owner(q.from_user.id):
            await q.answer("فقط Owner.", show_alert=True); return
        context.user_data["state"] = "test_duration"
        await q.edit_message_text("⏱ مدت تست را به ساعت بفرست.\nحداقل 1 و حداکثر 168 ساعت.", reply_markup=back_admin())
        return
    if k == "test_one_per_user":
        if not is_owner(q.from_user.id):
            await q.answer("فقط Owner.", show_alert=True); return
        normalize_test_data()
        _test_settings()["test_one_per_user"] = not _test_settings().get("test_one_per_user", True)
        await save_data()
        return await admin_test_settings(q)
    if k == "test_add_configs":
        if not can_manage_services(q.from_user.id):
            await q.edit_message_text("⛔ دسترسی ندارید.", reply_markup=back_admin()); return
        context.user_data["state"] = "add_test_configs"
        await q.edit_message_text(
            "➕ افزودن کانفیگ تست\n\nهر کانفیگ را در یک خط جدا بفرست.\n\n"
            "مثال:\nvless://...\nvmess://...\n\n"
            "♻️ موارد تکراری خودکار حذف می‌شوند.\n"
            "🔐 موجودی تست از موجودی فروش کاملاً جداست.\n/cancel برای لغو",
            reply_markup=back_admin(),
        )
        return

    if k.startswith("broadcast_target:"):
        role=get_admin_role(q.from_user.id)
        if not can_broadcast(q.from_user.id): await q.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_admin()); return
        return await broadcast_target_start(q,context,k.split(":",1)[1])
    if k=="admin_broadcast": return await admin_broadcast_menu(q,context)
    if k=="admin_home":
        role=get_admin_role(q.from_user.id)
        if not role: await q.edit_message_text("⛔ دسترسی ندارید.",reply_markup=back_home()); return
        await q.edit_message_text(f"👑 پنل مدیریت {BRAND}\n\nسطح دسترسی: {role}",reply_markup=admin_keyboard(role)); return
    # Existing routes use the improved global functions.
    return await BASE_CALLBACK_HANDLER(update,context)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    u=await ensure_user(update.effective_user); user_keyboard.current_user_id=update.effective_user.id
    if u.get("blocked") and not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی شما مسدود است."); return
    text=(update.message.text or "").strip(); state=context.user_data.get("state")
    if text=="/cancel":
        context.user_data.clear(); await update.message.reply_text("✅ عملیات لغو شد.",reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)) if get_admin_role(update.effective_user.id) else user_keyboard()); return
    if state=="wallet_topup_amount":
        try: amount=int(re.sub(r"[^0-9]","",text))
        except: amount=0
        return await add_topup_request(update,amount,context)
    if state=="config_search":
        context.user_data.clear(); return await handle_config_search(update,text)
    # 🧪 TEST CONFIG IMPORT
    if state == "add_test_configs":
        if not can_manage_services(update.effective_user.id):
            context.user_data.clear(); await update.message.reply_text("⛔ دسترسی ندارید."); return
        normalize_test_data()
        candidates = [x.strip() for x in text.splitlines() if x.strip()]
        added = duplicate = 0
        for cfg in candidates:
            if test_config_duplicate(cfg):
                duplicate += 1
                continue
            item_id = uid("testcfg")
            data["test_configs"][item_id] = {
                "id": item_id, "config": cfg, "status": "free",
                "assigned_to": None, "claim_id": None, "assigned_at": None, "created_at": now_iso()
            }
            added += 1
        context.user_data.clear(); await save_data()
        await update.message.reply_text(
            f"✅ موجودی تست بروزرسانی شد.\n\n➕ اضافه‌شده: {added}\n♻️ تکراری: {duplicate}\n🟢 موجودی آزاد: {test_free_count()}",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    # 🧪 GRANT EXTRA TEST BY USER ID / USERNAME
    if state == "test_grant":
        if not can_manage_users(update.effective_user.id):
            context.user_data.clear(); await update.message.reply_text("⛔ دسترسی ندارید."); return
        target = find_user_identifier(text)
        if not target:
            await update.message.reply_text("❌ کاربر پیدا نشد. ID یا username صحیح بفرست.", reply_markup=back_admin())
            return
        context.user_data["state"] = f"test_grant_count:{target['id']}"
        await update.message.reply_text(
            f"👤 کاربر پیدا شد: {target.get('first_name') or target.get('username') or target['id']}\n"
            f"🆔 {target['id']}\n"
            f"🧪 تست‌های قبلی: {target.get('test_claim_count', 0)}\n"
            f"🎁 مجوز اضافه فعلی: {target.get('test_extra_credits', 0)}\n\n"
            "چند تست اضافه مجاز شود؟\nمثال: 1",
            reply_markup=back_admin(),
        )
        return

    if state and state.startswith("test_grant_count:"):
        if not can_manage_users(update.effective_user.id):
            context.user_data.clear(); await update.message.reply_text("⛔ دسترسی ندارید."); return
        target_id = state.split(":",1)[1]
        target = data.get("users",{}).get(str(target_id))
        try:
            count = int(text)
            if count < 1 or count > 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ تعداد باید بین 1 تا 100 باشد.")
            return
        if not target:
            context.user_data.clear(); await update.message.reply_text("❌ کاربر پیدا نشد.", reply_markup=back_admin()); return
        target["test_extra_credits"] = int(target.get("test_extra_credits",0)) + count
        add_audit(update.effective_user.id, "grant_test", target_id, f"count={count}")
        context.user_data.clear(); await save_data()
        await update.message.reply_text(
            f"✅ مجوز تست اضافه شد.\n\n👤 کاربر: {target_id}\n🎁 تعداد مجاز اضافه‌شده: {count}\n🎁 موجودی مجوز اضافه فعلی: {target.get('test_extra_credits',0)}",
            reply_markup=admin_keyboard(get_admin_role(update.effective_user.id)),
        )
        return

    if state == "test_duration":
        if not is_owner(update.effective_user.id):
            context.user_data.clear(); await update.message.reply_text("⛔ فقط Owner."); return
        try:
            hours = int(text)
            if hours < 1 or hours > TEST_MAX_DURATION_HOURS:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ عدد باید بین 1 تا 168 باشد.")
            return
        _test_settings()["test_duration_hours"] = hours
        context.user_data.clear(); await save_data()
        await update.message.reply_text(f"✅ مدت تست روی {hours} ساعت تنظیم شد.", reply_markup=admin_keyboard("owner"))
        return

    if state and state.startswith("broadcast_text:"):
        target=state.split(":",1)[1]; context.user_data.clear(); return await run_targeted_broadcast(update,text,context,target)
    if text.startswith("/discount "):
        return await apply_discount(update,text.split(" ",1)[1].strip())
    # Keep legacy states intact.
    return await BASE_MESSAGE_HANDLER(update,context)


# Add a little safety to maintenance mode when an existing user is active.


if __name__ == "__main__":
    asyncio.run(main())
