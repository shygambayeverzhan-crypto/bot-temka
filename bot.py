import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or 0)
ADMIN_SETUP_CODE = os.getenv("ADMIN_SETUP_CODE", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY are missing")

db: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class Deposit(StatesGroup):
    amount = State()
    receipt = State()

def home_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💳 Пополнить баланс", callback_data="menu:deposit")
    b.button(text="💰 Баланс", callback_data="menu:balance")
    b.button(text="📜 История", callback_data="menu:history")
    b.button(text="💳 Реквизиты", callback_data="menu:wallets")
    b.button(text="🌐 Язык", callback_data="menu:language")
    b.button(text="🔄 Обновить", callback_data="menu:home")
    b.adjust(1, 2, 2, 1)
    return b.as_markup()

def back_kb():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Главное меню", callback_data="menu:home")
    return b.as_markup()

def balance_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💳 Пополнить", callback_data="menu:deposit")
    b.button(text="📜 История", callback_data="menu:history")
    b.button(text="⬅️ Главное меню", callback_data="menu:home")
    b.adjust(1, 2)
    return b.as_markup()

MAIN_KB = home_kb()

def now():
    return datetime.now(timezone.utc).isoformat()

def money(v):
    return f"{Decimal(str(v)):,.2f}".replace(",", " ").replace(".00", "")

def get_user(tg_id):
    r = db.table("bot_users").select("*").eq("telegram_id", tg_id).limit(1).execute()
    return r.data[0] if r.data else None

def ensure_user(tg):
    row = get_user(tg.id)
    data = {"telegram_id": tg.id, "username": tg.username, "first_name": tg.first_name, "last_name": tg.last_name, "updated_at": now()}
    if row:
        db.table("bot_users").update(data).eq("telegram_id", tg.id).execute()
        row.update(data)
        return row
    data.update({"language": "ru", "balance": 0, "is_blocked": False})
    return db.table("bot_users").insert(data).execute().data[0]

def admin_ids():
    ids = {ADMIN_TELEGRAM_ID} if ADMIN_TELEGRAM_ID else set()
    r = db.table("bot_admins").select("telegram_id").eq("is_active", True).execute()
    ids |= {int(x["telegram_id"]) for x in (r.data or [])}
    return ids

def is_admin(tg_id):
    return tg_id in admin_ids()

def active_wallet():
    r = db.table("wallets").select("*").eq("is_active", True).order("sort_order").order("created_at").limit(1).execute()
    return r.data[0] if r.data else None

def open_order(user_id):
    r = (db.table("orders").select("*").eq("user_id", user_id)
         .in_("status", ["pending", "waiting_receipt", "under_review"])
         .order("created_at", desc=True).limit(1).execute())
    return r.data[0] if r.data else None

async def notify_user(tg_id, text):
    try:
        await bot.send_message(tg_id, text)
    except Exception:
        logging.exception("notify_user failed")

def home_text(u):
    name = u.get("first_name") or "друг"
    balance_value = money(u["balance"])
    return (
        f"👋 <b>4% TRADER</b>\n"
        f"<i>Ваш личный финансовый кабинет</i>\n\n"
        f"Привет, <b>{name}</b>!\n\n"
        f"💰 <b>Баланс</b>\n"
        f"<code>{balance_value} ₸</code>\n\n"
        f"🟢 <b>Аккаунт активен</b>\n"
        f"Выберите действие ниже."
    )

async def show_home(target, tg):
    u = ensure_user(tg)
    text = home_text(u)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=home_kb())
        await target.answer()
    else:
        await target.answer(text, reply_markup=home_kb())

async def start_deposit_flow(message, state, tg):
    u = ensure_user(tg)
    if u["is_blocked"]:
        await message.answer("⛔ <b>Доступ ограничен.</b>", reply_markup=back_kb())
        return
    if open_order(u["id"]):
        await message.answer(
            "⚠️ <b>У вас уже есть открытая заявка.</b>\n\n"
            "Завершите текущую заявку перед созданием новой.",
            reply_markup=back_kb(),
        )
        return
    if not active_wallet():
        await message.answer(
            "⚠️ <b>Реквизиты пока не настроены.</b>\n\n"
            "Пополнение временно недоступно.",
            reply_markup=back_kb(),
        )
        return
    await state.set_state(Deposit.amount)
    await message.answer(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Введите сумму в тенге.\n"
        "Минимум: <b>100 ₸</b>\n"
        "Максимум: <b>10 000 000 ₸</b>\n\n"
        "Напишите, например: <code>50000</code>",
        reply_markup=back_kb(),
    )

@dp.callback_query(F.data == "menu:home")
async def menu_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(call, call.from_user)

@dp.callback_query(F.data == "menu:balance")
async def menu_balance(call: CallbackQuery):
    u = ensure_user(call.from_user)
    await call.message.edit_text(
        f"💰 <b>Ваш баланс</b>\n\n"
        f"<code>{money(u['balance'])} ₸</code>\n\n"
        f"Средства доступны после подтверждения администратором.",
        reply_markup=balance_kb(),
    )
    await call.answer()

@dp.callback_query(F.data == "menu:history")
async def menu_history(call: CallbackQuery):
    u = ensure_user(call.from_user)
    rows = db.table("balance_transactions").select("*").eq("user_id", u["id"]).order("created_at", desc=True).limit(8).execute().data or []
    if not rows:
        text = "📜 <b>История операций</b>\n\nПока операций нет."
    else:
        lines = ["📜 <b>История операций</b>", ""]
        for x in rows:
            amount = Decimal(str(x["amount"]))
            sign = "+" if amount > 0 else ""
            date = str(x["created_at"]).replace("T", " ")[:16]
            label = {"deposit": "Пополнение", "withdrawal": "Вывод", "adjustment": "Корректировка"}.get(x["type"], x["type"])
            lines.append(f"<code>{date}</code>  <b>{sign}{money(amount)} ₸</b>\n{label}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()

@dp.callback_query(F.data == "menu:wallets")
async def menu_wallets(call: CallbackQuery):
    rows = db.table("wallets").select("*").eq("is_active", True).order("sort_order").execute().data or []
    if not rows:
        text = "💳 <b>Реквизиты</b>\n\n⚠️ Активных реквизитов сейчас нет."
    else:
        parts = ["💳 <b>Реквизиты для пополнения</b>", ""]
        for x in rows:
            parts.append(
                f"🏦 <b>{x['bank_name']}</b>\n"
                f"<code>{x['requisites']}</code>\n"
                f"Получатель: <b>{x.get('holder_name') or '—'}</b>\n"
            )
        text = "\n".join(parts)
    await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()

@dp.callback_query(F.data == "menu:language")
async def menu_language(call: CallbackQuery):
    await call.message.edit_text(
        "🌐 <b>Язык</b>\n\nСейчас доступен русский язык.",
        reply_markup=back_kb(),
    )
    await call.answer()

@dp.callback_query(F.data == "menu:deposit")
async def menu_deposit(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await start_deposit_flow(call.message, state, call.from_user)

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    u = ensure_user(message.from_user)
    if u["is_blocked"]:
        await message.answer("⛔ <b>Доступ ограничен.</b>")
        return
    extra = "\n\n👨‍💻 <b>Админ:</b> /admin" if is_admin(message.from_user.id) else ""
    await message.answer(home_text(u) + extra, reply_markup=home_kb())

@dp.message(F.text == "💰 Мой баланс")
async def balance(message: Message):
    u = ensure_user(message.from_user)
    await message.answer(f"💰 <b>Ваш баланс</b>\n\n<code>{money(u['balance'])} ₸</code>", reply_markup=balance_kb())

@dp.message(F.text == "📜 История")
async def history(message: Message):
    u = ensure_user(message.from_user)
    r = db.table("balance_transactions").select("*").eq("user_id", u["id"]).order("created_at", desc=True).limit(10).execute()
    rows = r.data or []
    if not rows:
        await message.answer("📜 <b>История операций</b>\n\nПока операций нет.", reply_markup=back_kb())
        return
    lines = ["📜 <b>История операций</b>", ""]
    for x in rows:
        amount = Decimal(str(x["amount"]))
        sign = "+" if amount > 0 else ""
        lines.append(f"{str(x['created_at']).replace('T',' ')[:16]} — {sign}{money(amount)} ₸ — {x['type']}")
    await message.answer("\n".join(lines), reply_markup=back_kb())

@dp.message(F.text == "💳 Реквизиты")
async def wallets(message: Message):
    rows = db.table("wallets").select("*").eq("is_active", True).order("sort_order").execute().data or []
    if not rows:
        await message.answer("💳 <b>Реквизиты</b>\n\n⚠️ Активных реквизитов сейчас нет.", reply_markup=back_kb())
        return
    text = "💳 <b>Реквизиты для пополнения</b>\n"
    for x in rows:
        text += f"\n<b>{x['title']}</b>\n{x['bank_name']}\n<code>{x['requisites']}</code>\nПолучатель: {x.get('holder_name') or '—'}\n"
    await message.answer(text, reply_markup=back_kb())

@dp.message(F.text == "🌐 Язык")
async def language(message: Message):
    await message.answer("🌐 <b>Язык</b>\n\nСейчас доступен русский язык.", reply_markup=back_kb())

@dp.message(F.text == "💳 Пополнить баланс")
async def deposit_start(message: Message, state: FSMContext):
    await start_deposit_flow(message, state, message.from_user)

@dp.message(Deposit.amount)
async def deposit_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("receipt_order_id"):
        return
    raw = (message.text or "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        await message.answer("❌ <b>Неверная сумма.</b>\n\nВведите число, например <code>50000</code>.")
        return
    if amount < 100 or amount > 10_000_000:
        await message.answer("❌ <b>Сумма вне диапазона.</b>\n\nВведите от <b>100 ₸</b> до <b>10 000 000 ₸</b>.")
        return
    u = ensure_user(message.from_user)
    wallet = active_wallet()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    snapshot = {k: wallet.get(k) for k in ["id", "title", "bank_name", "method", "requisites", "holder_name", "currency"]}
    try:
        order = db.table("orders").insert({
            "user_id": u["id"], "wallet_id": wallet["id"], "wallet_snapshot": snapshot,
            "amount": float(amount), "currency": "KZT", "status": "pending", "expires_at": expires.isoformat()
        }).execute().data[0]
    except Exception:
        await message.answer("❌ Не удалось создать заявку. Попробуйте ещё раз.")
        return
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="📎 Я оплатил — отправить чек", callback_data=f"receipt:{order['id']}")
    b.button(text="❌ Отменить", callback_data=f"cancel:{order['id']}")
    b.adjust(1)
    await message.answer(
        f"🧾 <b>Заявка #{order['order_number']}</b>\n\n"
        f"💰 Сумма: <b>{money(amount)} ₸</b>\n"
        f"🏦 Банк: <b>{wallet['bank_name']}</b>\n"
        f"👤 Получатель: <b>{wallet.get('holder_name') or '—'}</b>\n\n"
        f"<b>Реквизиты</b>\n<code>{wallet['requisites']}</code>\n\n"
        f"⏳ Действуют <b>10 минут</b>. После оплаты отправьте чек.",
        reply_markup=b.as_markup()
    )

@dp.callback_query(F.data.startswith("receipt:"))
async def receipt_request(call: CallbackQuery, state: FSMContext):
    order_id = call.data.split(":", 1)[1]
    order = db.table("orders").select("*, bot_users(telegram_id)").eq("id", order_id).limit(1).execute().data
    if not order:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    order = order[0]
    u = get_user(call.from_user.id)
    if not u or order["user_id"] != u["id"]:
        await call.answer("Нет доступа", show_alert=True)
        return
    if order["status"] in {"paid", "cancelled", "expired"}:
        await call.answer("Заявка уже закрыта", show_alert=True)
        return
    await state.set_state(Deposit.receipt)
    await state.update_data(receipt_order_id=order_id)
    await call.message.answer("📎 <b>Отправьте чек об оплате</b>\n\nПодойдёт фото или PDF-документ.", reply_markup=back_kb())
    await call.answer()

async def store_receipt(message, state):
    data = await state.get_data()
    order_id = data.get("receipt_order_id")
    if not order_id:
        return False
    order = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not order or order[0]["status"] in {"paid", "cancelled", "expired"}:
        return False
    file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
    unique_id = message.photo[-1].file_unique_id if message.photo else (message.document.file_unique_id if message.document else None)
    if not file_id:
        return False
    db.table("receipts").insert({"order_id": order_id, "telegram_file_id": file_id, "telegram_file_unique_id": unique_id, "caption": message.caption}).execute()
    db.table("orders").update({"status": "under_review", "updated_at": now()}).eq("id", order_id).execute()
    await state.clear()
    return order[0]

@dp.message(Deposit.receipt, F.photo)
async def receipt_photo(message: Message, state: FSMContext):
    order = await store_receipt(message, state)
    if not order:
        await message.answer("❌ Не удалось принять чек.")
        return
    await message.answer("✅ <b>Чек получен.</b>\n\nЗаявка передана на проверку. После подтверждения сумма будет зачислена на баланс.", reply_markup=home_kb())
    await notify_admins(order["id"])

@dp.message(Deposit.receipt, F.document)
async def receipt_document(message: Message, state: FSMContext):
    order = await store_receipt(message, state)
    if not order:
        await message.answer("❌ Не удалось принять чек.")
        return
    await message.answer("✅ Чек получен. Заявка отправлена администратору.", reply_markup=home_kb())
    await notify_admins(order["id"])

async def notify_admins(order_id):
    olist = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not olist:
        return
    o = olist[0]
    ulist = db.table("bot_users").select("*").eq("id", o["user_id"]).limit(1).execute().data
    u = ulist[0] if ulist else {}
    rlist = db.table("receipts").select("*").eq("order_id", order_id).order("uploaded_at", desc=True).limit(1).execute().data
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"confirm:{order_id}")
    b.button(text="❌ Отклонить", callback_data=f"reject:{order_id}")
    b.adjust(2)
    text = f"🔔 <b>Заявка #{o['order_number']}</b>\n\nПользователь: @{u.get('username') or 'без_username'}\nTelegram ID: <code>{u.get('telegram_id')}</code>\nСумма: <b>{money(o['amount'])} ₸</b>"
    for aid in admin_ids():
        try:
            await bot.send_message(aid, text, reply_markup=b.as_markup())
            if rlist:
                await bot.send_photo(aid, rlist[0]["telegram_file_id"], caption=f"Чек по заявке #{o['order_number']}")
        except Exception:
            logging.exception("Admin notify failed")

@dp.callback_query(F.data.startswith("confirm:"))
async def confirm_first(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    order_id = call.data.split(":", 1)[1]
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, подтвердить", callback_data=f"confirm2:{order_id}")
    b.button(text="↩️ Назад", callback_data=f"noop:{order_id}")
    await call.message.edit_reply_markup(reply_markup=b.as_markup())
    await call.answer("Подтвердите начисление вторым нажатием.")

@dp.callback_query(F.data.startswith("noop:"))
async def noop(call: CallbackQuery):
    await call.answer("Отменено.")

@dp.callback_query(F.data.startswith("confirm2:"))
async def confirm_second(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    order_id = call.data.split(":", 1)[1]
    try:
        result = db.rpc("confirm_order", {"p_order_id": order_id, "p_admin_telegram_id": call.from_user.id}).execute().data or {}
    except Exception:
        logging.exception("confirm_order failed")
        await call.answer("Ошибка подтверждения", show_alert=True)
        return
    if result.get("ok") is not True:
        await call.answer("Заказ не подтверждён", show_alert=True)
        return
    if not result.get("already_paid"):
        await notify_user(int(result["telegram_id"]), f"✅ <b>Оплата подтверждена</b>\n\nЗачислено: <b>{money(result['amount'])} ₸</b>\nБаланс: <b>{money(result['balance'])} ₸</b>")
        o = db.table("orders").select("user_id").eq("id", order_id).limit(1).execute().data
        db.table("audit_logs").insert({"actor_telegram_id": call.from_user.id, "action": "confirm_order", "order_id": order_id, "target_user_id": o[0]["user_id"] if o else None, "payload": {"amount": result["amount"]}}).execute()
    await call.message.edit_text("✅ <b>Заявка подтверждена. Баланс зачислен.</b>")
    await call.answer("Готово.")

@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    order_id = call.data.split(":", 1)[1]
    olist = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not olist:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    o = olist[0]
    if o["status"] == "paid":
        await call.answer("Уже оплачена", show_alert=True)
        return
    db.table("orders").update({"status": "cancelled", "cancelled_at": now(), "cancelled_reason": "Rejected by admin", "updated_at": now()}).eq("id", order_id).execute()
    u = db.table("bot_users").select("telegram_id").eq("id", o["user_id"]).limit(1).execute().data
    if u:
        await notify_user(int(u[0]["telegram_id"]), f"❌ Заявка #{o['order_number']} отклонена администратором.")
    db.table("audit_logs").insert({"actor_telegram_id": call.from_user.id, "action": "reject_order", "order_id": order_id, "target_user_id": o["user_id"]}).execute()
    await call.message.edit_text(f"❌ <b>Заявка #{o['order_number']} отклонена.</b>")
    await call.answer()

@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_order(call: CallbackQuery):
    order_id = call.data.split(":", 1)[1]
    olist = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    u = get_user(call.from_user.id)
    if not olist or not u or olist[0]["user_id"] != u["id"]:
        await call.answer("Нет доступа", show_alert=True)
        return
    o = olist[0]
    if o["status"] in {"paid", "cancelled", "expired"}:
        await call.answer("Уже закрыта", show_alert=True)
        return
    db.table("orders").update({"status": "cancelled", "cancelled_at": now(), "cancelled_reason": "Cancelled by user", "updated_at": now()}).eq("id", order_id).execute()
    await call.message.edit_text(f"❌ Заявка #{o['order_number']} отменена.")
    await call.answer()

@dp.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ <b>Операция отменена.</b>", reply_markup=home_kb())

@dp.message(Command("claim_admin"))
async def claim_admin(message: Message):
    if not ADMIN_SETUP_CODE:
        await message.answer("Админ-активация отключена.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or parts[1] != ADMIN_SETUP_CODE:
        await message.answer("❌ Неверный код.")
        return
    db.table("bot_admins").upsert({"telegram_id": message.from_user.id, "role": "superadmin", "is_active": True}, on_conflict="telegram_id").execute()
    await message.answer("✅ Вы назначены администратором.")

@dp.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    pending = db.table("orders").select("id", count="exact").eq("status", "under_review").execute()
    total = db.table("bot_users").select("id", count="exact").execute()
    await message.answer(f"👨‍💻 <b>Админ-панель</b>\n\n📥 На проверке: <b>{pending.count or 0}</b>\n👥 Пользователей: <b>{total.count or 0}</b>\n\n/orders — заявки\n/wallet_add Название|Банк|Реквизиты|Получатель\n/wallet_off UUID")

@dp.message(Command("orders"))
async def orders(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    rows = db.table("orders").select("*").eq("status", "under_review").order("created_at").limit(20).execute().data or []
    if not rows:
        await message.answer("📭 Заявок на проверке нет.")
        return
    for o in rows:
        b = InlineKeyboardBuilder()
        b.button(text="✅ Подтвердить", callback_data=f"confirm:{o['id']}")
        b.button(text="❌ Отклонить", callback_data=f"reject:{o['id']}")
        b.adjust(2)
        await message.answer(f"#{o['order_number']} — <b>{money(o['amount'])} ₸</b>", reply_markup=b.as_markup())

@dp.message(Command("wallet_add"))
async def wallet_add(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    p = (message.text or "").split(maxsplit=1)
    if len(p) != 2 or len(p[1].split("|")) != 4:
        await message.answer("Формат: /wallet_add Название|Банк|Реквизиты|Получатель")
        return
    title, bank, req, holder = [x.strip() for x in p[1].split("|")]
    db.table("wallets").insert({"title": title, "bank_name": bank, "requisites": req, "holder_name": holder, "currency": "KZT", "is_active": True}).execute()
    await message.answer("✅ Реквизит добавлен.")

@dp.message(Command("wallet_off"))
async def wallet_off(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    p = (message.text or "").split(maxsplit=1)
    if len(p) != 2:
        await message.answer("Формат: /wallet_off UUID")
        return
    db.table("wallets").update({"is_active": False, "updated_at": now()}).eq("id", p[1].strip()).execute()
    await message.answer("✅ Реквизит отключён.")

async def expiry_loop():
    while True:
        try:
            current = now()
            rows = db.table("orders").select("id,order_number,user_id").in_("status", ["pending","waiting_receipt"]).lt("expires_at", current).limit(100).execute().data or []
            for o in rows:
                db.table("orders").update({"status":"expired","updated_at":current}).eq("id",o["id"]).execute()
                u = db.table("bot_users").select("telegram_id").eq("id",o["user_id"]).limit(1).execute().data
                if u:
                    await notify_user(int(u[0]["telegram_id"]), f"⌛ Заявка #{o['order_number']} истекла.")
        except Exception:
            logging.exception("expiry loop")
        await asyncio.sleep(30)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(expiry_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
