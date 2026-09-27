import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, CopyTextButton, Message
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

MIN_DEPOSIT = Decimal("250")
THRESHOLD = Decimal("500")
FEE_RATE = Decimal("0.04")
ADMIN_TRC20_ADDRESS = os.getenv("ADMIN_TRC20_ADDRESS", "TNTuGJ2LrGR9va5ywJq8zAx8CxjVUJRMzP").strip()

class Deposit(StatesGroup):
    amount = State()
    txid = State()

class Withdrawal(StatesGroup):
    address = State()

class P2P(StatesGroup):
    amount = State()
    destination = State()
    receipt = State()

class TraderSetup(StatesGroup):
    bank_name = State()
    card_holder = State()
    card_requisites = State()
    usdt_address = State()

def register_kb():
    b = InlineKeyboardBuilder(); b.button(text="📝 Регистрация", callback_data="register"); return b.as_markup()

def home_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💰 Внести депозит", callback_data="menu:deposit")
    b.button(text="💳 Баланс", callback_data="menu:balance")
    b.button(text="📤 Вывести средства", callback_data="menu:withdraw")
    b.button(text="📜 История", callback_data="menu:history")
    b.button(text="ℹ️ Помощь", callback_data="menu:help")
    b.button(text="💱 P2P обмен", callback_data="menu:p2p")
    b.adjust(1,2,2,1); return b.as_markup()

def back_kb():
    b=InlineKeyboardBuilder(); b.button(text="⬅️ Главное меню",callback_data="menu:home"); return b.as_markup()

def address_copy_kb(address):
    b = InlineKeyboardBuilder()
    b.button(
        text="📋 Скопировать TRC20-адрес",
        copy_text=CopyTextButton(text=address),
    )
    b.button(text="⬅️ Главное меню", callback_data="menu:home")
    b.adjust(1, 1)
    return b.as_markup()


def balance_kb():
    b=InlineKeyboardBuilder(); b.button(text="💰 Внести депозит",callback_data="menu:deposit"); b.button(text="📤 Вывести средства",callback_data="menu:withdraw"); b.button(text="⬅️ Главное меню",callback_data="menu:home"); b.adjust(1,2); return b.as_markup()

def now():
    return datetime.now(timezone.utc).isoformat()

def money(v):
    return f"{Decimal(str(v)):,.2f}".replace(",", " ").replace(".00", "")

def get_user(tg_id):
    r = db.table("bot_users").select("*").eq("telegram_id", tg_id).limit(1).execute()
    return r.data[0] if r.data else None

def ensure_user(tg):
    row = get_user(tg.id)
    if not row or not row.get("registered_at"):
        return None
    data = {"telegram_id": tg.id, "username": tg.username, "first_name": tg.first_name, "last_name": tg.last_name, "updated_at": now()}
    db.table("bot_users").update(data).eq("telegram_id", tg.id).execute()
    row.update(data)
    return row


def register_user(tg):
    """Create or activate a user's account; safe to call more than once."""
    existing = get_user(tg.id)
    data = {
        "telegram_id": tg.id,
        "username": tg.username,
        "first_name": tg.first_name,
        "last_name": tg.last_name,
        "updated_at": now(),
    }
    if existing:
        if not existing.get("registered_at"):
            data["registered_at"] = now()
        db.table("bot_users").update(data).eq("telegram_id", tg.id).execute()
    else:
        data.update({"balance": 0, "is_blocked": False, "registered_at": now()})
        db.table("bot_users").insert(data).execute()
    return get_user(tg.id)
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
        f"<code>{balance_value} USDT</code>\n\n"
        f"🟢 <b>Аккаунт активен</b>\n"
        f"Выберите действие ниже."
    )

async def show_home(target,tg):
    u=ensure_user(tg)
    if not u:
        text="👋 <b>Добро пожаловать!</b>\n\nДля начала работы зарегистрируйтесь."
        if isinstance(target,CallbackQuery): await target.message.edit_text(text,reply_markup=register_kb()); await target.answer()
        else: await target.answer(text,reply_markup=register_kb())
        return
    text=home_text(u) if not u["is_blocked"] else "⛔ <b>Доступ ограничен.</b>"
    if isinstance(target,CallbackQuery): await target.message.edit_text(text,reply_markup=home_kb()); await target.answer()
    else: await target.answer(text,reply_markup=home_kb())

async def start_deposit_flow(message,state,tg):
    u=ensure_user(tg)
    if not u: await message.answer("👋 Сначала зарегистрируйтесь.",reply_markup=register_kb()); return
    if u["is_blocked"]: await message.answer("⛔ <b>Доступ ограничен.</b>",reply_markup=back_kb()); return
    if Decimal(str(u["balance"]))>=THRESHOLD: await message.answer("⚠️ <b>Пополнение недоступно.</b>\nСначала необходимо вывести весь баланс.",reply_markup=back_kb()); return
    if open_order(u["id"]): await message.answer("⚠️ У вас уже есть открытая заявка на депозит.",reply_markup=back_kb()); return
    await state.set_state(Deposit.amount)
    await message.answer(f"💰 <b>Пополнение</b>\n\nВведите сумму USDT.\nМинимум: <b>{money(MIN_DEPOSIT)} USDT</b>\nСеть: <b>TRC20</b>",reply_markup=back_kb())

@dp.callback_query(F.data == "menu:home")
async def menu_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(call, call.from_user)

@dp.callback_query(F.data == "menu:balance")
async def menu_balance(call: CallbackQuery):
    u=ensure_user(call.from_user)
    if not u: await call.answer("Сначала зарегистрируйтесь.",show_alert=True); return
    await call.message.edit_text(f"💳 <b>Ваш баланс</b>\n\n<b>{money(u['balance'])} USDT</b>\nСеть: <b>TRC20</b>",reply_markup=balance_kb()); await call.answer()

@dp.callback_query(F.data == "menu:withdraw")
async def menu_withdraw(call: CallbackQuery,state:FSMContext):
    u=ensure_user(call.from_user)
    if not u: await call.answer("Сначала зарегистрируйтесь.",show_alert=True); return
    bal=Decimal(str(u["balance"]))
    if bal<THRESHOLD: await call.message.edit_text(f"📤 <b>Вывод</b>\n\nВывод доступен при балансе от <b>500 USDT</b>.\nСейчас: <b>{money(bal)} USDT</b>.",reply_markup=back_kb()); await call.answer(); return
    if open_withdrawal(u["id"]): await call.message.edit_text("⏳ У вас уже есть заявка на вывод.",reply_markup=back_kb()); await call.answer(); return
    fee=(bal*FEE_RATE).quantize(Decimal("0.01"),rounding=ROUND_DOWN); net=bal-fee
    await state.set_state(Withdrawal.address)
    await call.message.edit_text(f"📤 <b>Обязательный вывод</b>\n\nБаланс: <b>{money(bal)} USDT</b>\nКомиссия 4%: <b>{money(fee)} USDT</b>\nК получению: <b>{money(net)} USDT</b>\n\nВведите адрес USDT TRC20:",reply_markup=back_kb()); await call.answer()

@dp.callback_query(F.data == "menu:help")
async def menu_help(call: CallbackQuery):
    await call.message.edit_text("ℹ️ <b>Правила</b>\n\n• Регистрация обязательна.\n• Минимальный депозит — <b>250 USDT</b>.\n• Сеть — <b>TRC20</b>.\n• Депозит подтверждает администратор.\n• При достижении 500 USDT требуется полный вывод.\n• Комиссия вывода — 4%.\n\nПример: <b>500 → 20 комиссии → 480 USDT пользователю.</b>",reply_markup=back_kb()); await call.answer()

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
    await state.clear(); u=ensure_user(message.from_user)
    if not u: await message.answer("👋 <b>Добро пожаловать!</b>\n\nДля начала работы зарегистрируйтесь.",reply_markup=register_kb()); return
    if u["is_blocked"]: await message.answer("⛔ <b>Доступ ограничен.</b>"); return
    await message.answer(home_text(u),reply_markup=home_kb())

@dp.callback_query(F.data == "register")
async def register_cb(call: CallbackQuery):
    # Acknowledge immediately so Telegram stops showing the callback spinner.
    await call.answer()
    try:
        u = register_user(call.from_user)
        if not u:
            raise RuntimeError("Registration did not return a user row")
        if u.get("is_blocked"):
            await call.message.edit_text("⛔ <b>Доступ ограничен.</b>")
            return
        await call.message.edit_text(
            "✅ <b>Регистрация завершена.</b>\\n\\n" + home_text(u),
            reply_markup=home_kb(),
        )
    except Exception:
        logging.exception("registration failed for telegram_id=%s", call.from_user.id)
        await call.message.edit_text(
            "❌ Не удалось завершить регистрацию. Попробуйте ещё раз позже.",
            reply_markup=register_kb(),
        )

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
async def deposit_amount(message:Message,state:FSMContext):
    try: amount=Decimal((message.text or "").replace(" ","").replace(",",".")).quantize(Decimal("0.01"))
    except InvalidOperation: await message.answer("❌ Введите число, например <code>250</code>."); return
    if amount<MIN_DEPOSIT: await message.answer(f"❌ Минимальный депозит: <b>{money(MIN_DEPOSIT)} USDT</b>."); return
    u=ensure_user(message.from_user)
    if not u: await state.clear(); await message.answer("Сначала зарегистрируйтесь.",reply_markup=register_kb()); return
    if Decimal(str(u["balance"]))>=THRESHOLD: await state.clear(); await message.answer("⚠️ Сначала необходимо вывести весь баланс.",reply_markup=home_kb()); return
    try:
        o=db.table("orders").insert({"user_id":u["id"],"wallet_id":None,"wallet_snapshot":{"network":"TRC20","asset":"USDT","address":ADMIN_TRC20_ADDRESS},"amount":float(amount),"currency":"USDT","network":"TRC20","status":"pending","expires_at":(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()}).execute().data[0]
    except Exception: logging.exception("deposit create failed"); await message.answer("❌ Не удалось создать заявку."); return
    await state.set_state(Deposit.txid); await state.update_data(order_id=o["id"])
    await message.answer(f"🧾 <b>Заявка #{o['order_number']}</b>\n\nСумма: <b>{money(amount)} USDT</b>\nСеть: <b>TRC20</b>\n\nАдрес для оплаты:\n<code>{ADMIN_TRC20_ADDRESS}</code>\n\nПосле перевода отправьте <b>TXID</b>.",reply_markup=address_copy_kb(ADMIN_TRC20_ADDRESS))

@dp.message(Deposit.txid)
async def deposit_txid(message: Message, state: FSMContext):
    order_id = (await state.get_data()).get("order_id")
    if not order_id:
        await state.clear()
        await message.answer("Заявка не найдена. Начните пополнение заново.", reply_markup=home_kb())
        return

    rows = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not rows:
        await state.clear()
        await message.answer("Заявка не найдена.", reply_markup=home_kb())
        return

    receipt = None
    update = {"status": "under_review", "updated_at": now()}
    if message.text:
        txid = message.text.strip()
        if len(txid) < 20:
            await message.answer("❌ Отправьте корректный TXID TRC20 или приложите фото/файл чека.")
            return
        update["deposit_tx_hash"] = txid
    elif message.photo:
        receipt = ("photo", message.photo[-1].file_id)
    elif message.document:
        receipt = ("document", message.document.file_id)
    else:
        await message.answer("Отправьте TXID TRC20 текстом или приложите фото/файл чека.")
        return

    try:
        db.table("orders").update(update).eq("id", order_id).execute()
        delivered = await notify_admins(order_id, receipt=receipt)
    except Exception:
        logging.exception("deposit submission failed for order_id=%s", order_id)
        delivered = False

    if not delivered:
        await message.answer(
            "Заявка сохранена, но уведомление администратору не доставлено. "
            "Попробуйте отправить TXID или чек ещё раз — заявка останется той же."
        )
        return

    await state.clear()
    await message.answer(
        "✅ <b>Заявка передана администратору на проверку.</b>",
        reply_markup=home_kb(),
    )

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

def valid_tron_address(addr):
    alphabet="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"; return len(addr)==34 and addr.startswith("T") and all(c in alphabet for c in addr)

@dp.message(Withdrawal.address)
async def withdrawal_address(message:Message,state:FSMContext):
    addr=(message.text or "").strip()
    if not valid_tron_address(addr): await message.answer("❌ Некорректный адрес USDT TRC20."); return
    u=ensure_user(message.from_user)
    if not u: await state.clear(); await message.answer("Сначала зарегистрируйтесь.",reply_markup=register_kb()); return
    bal=Decimal(str(u["balance"]))
    if bal<THRESHOLD: await state.clear(); await message.answer("⚠️ Баланс уже изменился.",reply_markup=home_kb()); return
    fee=(bal*FEE_RATE).quantize(Decimal("0.01"),rounding=ROUND_DOWN); net=bal-fee
    try:
        wd=db.table("withdrawals").insert({"user_id":u["id"],"amount":float(bal),"fee_amount":float(fee),"net_amount":float(net),"currency":"USDT","destination_address":addr,"status":"pending","note":"Mandatory full withdrawal; 4% fee retained by admin."}).execute().data[0]
    except Exception: logging.exception("withdrawal create failed"); await message.answer("❌ Не удалось создать заявку."); return
    await state.clear(); await message.answer(f"📤 <b>Заявка создана</b>\n\nБаланс: <b>{money(bal)} USDT</b>\nКомиссия 4%: <b>{money(fee)} USDT</b>\nК получению: <b>{money(net)} USDT</b>\n\nАдрес:\n<code>{addr}</code>\n\nАдминистратор вручную отправит {money(net)} USDT и подтвердит выплату.",reply_markup=home_kb()); await notify_withdrawal_admins(wd["id"])

async def notify_withdrawal_admins(wid):
    rows=db.table("withdrawals").select("*").eq("id",wid).limit(1).execute().data
    if not rows:return
    w=rows[0]; urows=db.table("bot_users").select("*").eq("id",w["user_id"]).limit(1).execute().data; u=urows[0] if urows else {}
    b=InlineKeyboardBuilder(); b.button(text="✅ Выплачено / подтвердить",callback_data=f"wconfirm:{wid}"); b.button(text="❌ Отклонить",callback_data=f"wreject:{wid}"); b.adjust(1)
    text=f"📤 <b>Вывод</b>\n\nКлиент: @{u.get('username') or 'без_username'}\nСписать: <b>{money(w['amount'])} USDT</b>\nКомиссия: <b>{money(w['fee_amount'])} USDT</b>\nОтправить: <b>{money(w['net_amount'])} USDT</b>\nАдрес: <code>{w['destination_address']}</code>\n\nПосле ручной отправки нажмите кнопку подтверждения."
    for aid in admin_ids(): await notify_user(aid,text); await bot.send_message(aid,"Подтвердите выплату:",reply_markup=b.as_markup())

@dp.callback_query(F.data.startswith("wconfirm:"))
async def wconfirm(call:CallbackQuery):
    if not is_admin(call.from_user.id): await call.answer("Нет доступа",show_alert=True); return
    wid=call.data.split(":",1)[1]; result=db.rpc("confirm_withdrawal",{"p_withdrawal_id":wid,"p_admin_telegram_id":call.from_user.id}).execute().data or {}
    if result.get("ok") is not True: await call.answer(f"Ошибка: {result.get('error','unknown')}",show_alert=True); return
    await notify_user(int(result["telegram_id"]),f"✅ <b>Вывод подтверждён</b>\n\nПолучено: <b>{money(result['net_amount'])} USDT</b>\nКомиссия: <b>{money(result['fee_amount'])} USDT</b>"); await call.message.edit_text("✅ <b>Вывод подтверждён. Баланс списан.</b>"); await call.answer("Готово")

@dp.callback_query(F.data.startswith("wreject:"))
async def wreject(call:CallbackQuery):
    if not is_admin(call.from_user.id): await call.answer("Нет доступа",show_alert=True); return
    wid=call.data.split(":",1)[1]; rows=db.table("withdrawals").select("*").eq("id",wid).limit(1).execute().data
    if not rows: await call.answer("Заявка не найдена",show_alert=True); return
    w=rows[0]
    if w["status"]=="paid": await call.answer("Уже выплачено",show_alert=True); return
    db.table("withdrawals").update({"status":"rejected","processed_at":now(),"processed_by":call.from_user.id,"note":"Rejected by admin; balance unchanged."}).eq("id",wid).execute(); u=db.table("bot_users").select("telegram_id").eq("id",w["user_id"]).limit(1).execute().data
    if u: await notify_user(int(u[0]["telegram_id"]),"❌ Вывод отклонён. Баланс не изменён.")
    await call.message.edit_text("❌ <b>Вывод отклонён.</b>"); await call.answer()

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
async def admin(message:Message):
    if not is_admin(message.from_user.id): await message.answer("⛔ Нет доступа."); return
    p=db.table("orders").select("id",count="exact").eq("status","under_review").execute(); wd=db.table("withdrawals").select("id",count="exact").in_("status",["pending","processing"]).execute(); u=db.table("bot_users").select("id",count="exact").execute()
    await message.answer(f"👨‍💻 <b>Админ-панель</b>\n\n📥 Депозиты: <b>{p.count or 0}</b>\n📤 Выводы: <b>{wd.count or 0}</b>\n👥 Пользователи: <b>{u.count or 0}</b>\n\n/orders — депозиты\n/withdrawals — выводы\n/stats — комиссия")

@dp.message(Command("orders"))
async def orders(message:Message):
    if not is_admin(message.from_user.id): await message.answer("⛔ Нет доступа."); return
    rows=db.table("orders").select("*").eq("status","under_review").order("created_at").limit(20).execute().data or []
    if not rows: await message.answer("📭 Депозитов на проверке нет."); return
    for o in rows:
        b=InlineKeyboardBuilder(); b.button(text="✅ Подтвердить",callback_data=f"confirm:{o['id']}"); b.button(text="❌ Отклонить",callback_data=f"reject:{o['id']}"); b.adjust(2)
        await message.answer(f"#{o['order_number']} — <b>{money(o['amount'])} USDT</b>\nTXID: <code>{o.get('deposit_tx_hash') or '—'}</code>",reply_markup=b.as_markup())

@dp.message(Command("withdrawals"))
async def withdrawals(message:Message):
    if not is_admin(message.from_user.id): await message.answer("⛔ Нет доступа."); return
    rows=db.table("withdrawals").select("*").in_("status",["pending","processing"]).order("created_at").limit(20).execute().data or []
    if not rows: await message.answer("📭 Активных выводов нет."); return
    for w in rows:
        b=InlineKeyboardBuilder(); b.button(text="✅ Выплачено / подтвердить",callback_data=f"wconfirm:{w['id']}"); b.button(text="❌ Отклонить",callback_data=f"wreject:{w['id']}"); b.adjust(1)
        await message.answer(f"📤 <b>Вывод</b>\n\nСписать: {money(w['amount'])} USDT\nКомиссия: {money(w['fee_amount'])} USDT\nОтправить: <b>{money(w['net_amount'])} USDT</b>\nАдрес: <code>{w['destination_address']}</code>",reply_markup=b.as_markup())

@dp.message(Command("stats"))
async def stats(message:Message):
    if not is_admin(message.from_user.id): await message.answer("⛔ Нет доступа."); return
    rows=db.table("admin_transactions").select("amount").eq("type","withdrawal_fee").execute().data or []; total=sum((Decimal(str(x["amount"])) for x in rows),Decimal("0"))
    await message.answer(f"📊 <b>Комиссии 4%</b>\n\nУчтено: <b>{money(total)} USDT</b>")

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




async def notify_admins(order_id, receipt=None):
    rows = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not rows:
        logging.error("order %s was not found while notifying admins", order_id)
        return False
    o = rows[0]
    urows = db.table("bot_users").select("*").eq("id", o["user_id"]).limit(1).execute().data
    u = urows[0] if urows else {}
    admins = admin_ids()
    if not admins:
        logging.error("no active admins configured for deposit order %s", order_id)
        return False
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"confirm:{order_id}")
    b.button(text="❌ Отклонить", callback_data=f"reject:{order_id}")
    b.adjust(2)
    text_msg = (
        f"🔔 <b>Депозит #{o['order_number']}</b>\\n\\n"
        f"Клиент: @{u.get('username') or 'без_username'}\\n"
        f"Сумма: <b>{money(o['amount'])} USDT</b>\\n"
        f"Сеть: <b>TRC20</b>\\n"
        f"TXID: <code>{o.get('deposit_tx_hash') or '—'}</code>"
    )
    if receipt:
        text_msg += "\\n\\n📎 Пользователь приложил чек."
    delivered = 0
    for aid in admins:
        try:
            await bot.send_message(aid, text_msg, reply_markup=b.as_markup())
            if receipt:
                kind, file_id = receipt
                if kind == "photo":
                    await bot.send_photo(aid, file_id, caption=f"🧾 Чек к депозиту #{o['order_number']}")
                else:
                    await bot.send_document(aid, file_id, caption=f"🧾 Чек к депозиту #{o['order_number']}")
            delivered += 1
        except Exception:
            logging.exception("deposit notification failed for admin_id=%s order_id=%s", aid, order_id)
    return delivered == len(admins)




def p2p_settings():
    rows = db.table("p2p_settings").select("*").eq("id", 1).limit(1).execute().data or []
    return rows[0] if rows else None


def p2p_active_trade(user_id):
    rows = (db.table("p2p_trades").select("id,trade_number,status")
            .eq("customer_user_id", user_id)
            .in_("status", ["new", "accepted", "payment_reported", "payment_confirmed", "payout_sent"])
            .order("created_at", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else None


def p2p_payout_text(trade):
    amount = Decimal(str(trade["amount_usdt"]))
    fee = Decimal(str(trade["fee_usdt"]))
    rate = Decimal(str(trade["rate"]))
    if trade["direction"] == "usdt_to_kzt":
        return f"{money((amount - fee) * rate)} KZT"
    return f"{money(amount - fee)} USDT"


def p2p_menu_markup():
    b = InlineKeyboardBuilder()
    b.button(text="USDT → KZT", callback_data="p2p:direction:usdt_to_kzt")
    b.button(text="KZT → USDT", callback_data="p2p:direction:kzt_to_usdt")
    b.button(text="🧑‍💼 Стать трейдером", callback_data="p2p:trader_setup")
    b.button(text="⬅️ Главное меню", callback_data="menu:home")
    b.adjust(1, 1, 1, 1)
    return b.as_markup()


async def begin_trader_setup(message, tg, state):
    user = ensure_user(tg)
    if not user:
        await message.answer("Сначала зарегистрируйтесь.", reply_markup=register_kb())
        return
    if Decimal(str(user.get("balance") or 0)) < Decimal("250"):
        await message.answer("Для статуса трейдера нужен подтверждённый рабочий депозит от 250 USDT. Сначала внесите депозит и дождитесь подтверждения.")
        return
    existing = db.table("p2p_traders").select("id").eq("user_id", user["id"]).limit(1).execute().data or []
    if existing:
        db.table("p2p_traders").update({"status": "pending", "updated_at": now()}).eq("user_id", user["id"]).execute()
    else:
        db.table("p2p_traders").insert({"user_id": user["id"], "status": "pending"}).execute()
    await state.clear()
    await state.set_state(TraderSetup.bank_name)
    await message.answer("Введите название вашего банка, к которому привязана карта трейдера:")


@dp.message(Command("p2p"))
async def p2p_command(message: Message, state: FSMContext):
    await state.clear()
    user = ensure_user(message.from_user)
    if not user:
        await message.answer("Сначала зарегистрируйтесь.", reply_markup=register_kb())
        return
    settings = p2p_settings()
    rate = Decimal(str(settings["usdt_kzt_rate"])) if settings and settings.get("usdt_kzt_rate") else None
    if rate is None:
        await message.answer("💱 P2P-обмен временно закрыт: администратор ещё не задал курс.")
        return
    await message.answer(
        f"💱 <b>P2P-обмен USDT ↔ KZT</b>\nКурс: <b>1 USDT = {money(rate)} KZT</b>\nКомиссия: <b>4%</b>\n\n"
        "Перевод выполняют пользователи вручную. Не отправляйте деньги до принятия заявки трейдером и проверки реквизитов.",
        reply_markup=p2p_menu_markup(),
    )


@dp.callback_query(F.data == "menu:p2p")
async def p2p_menu_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user = ensure_user(call.from_user)
    if not user:
        await call.answer("Сначала зарегистрируйтесь.", show_alert=True)
        return
    settings = p2p_settings()
    rate = Decimal(str(settings["usdt_kzt_rate"])) if settings and settings.get("usdt_kzt_rate") else None
    if rate is None:
        await call.answer("Обмен пока закрыт: курс не задан администратором.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        f"💱 <b>P2P-обмен USDT ↔ KZT</b>\nКурс: <b>1 USDT = {money(rate)} KZT</b>\nКомиссия: <b>4%</b>\n\n"
        "Перевод выполняют пользователи вручную. Не отправляйте деньги до принятия заявки трейдером и проверки реквизитов.",
        reply_markup=p2p_menu_markup(),
    )


@dp.callback_query(F.data.startswith("p2p:direction:"))
async def p2p_choose_direction(call: CallbackQuery, state: FSMContext):
    await call.answer()
    user = ensure_user(call.from_user)
    if not user:
        await call.message.answer("Сначала зарегистрируйтесь.", reply_markup=register_kb())
        return
    settings = p2p_settings()
    if not settings or not settings.get("usdt_kzt_rate"):
        await call.message.answer("Обмен пока закрыт: курс не задан администратором.")
        return
    direction = call.data.rsplit(":", 1)[1]
    if direction not in {"usdt_to_kzt", "kzt_to_usdt"}:
        return
    if p2p_active_trade(user["id"]):
        await call.message.answer("У вас уже есть открытая P2P-заявка. Завершите или отмените её перед созданием новой.")
        return
    await state.clear()
    await state.update_data(p2p_direction=direction)
    await state.set_state(P2P.amount)
    await call.message.answer("Введите сумму обмена в USDT-эквиваленте (от 10 до 500 USDT):")


@dp.message(P2P.amount)
async def p2p_amount(message: Message, state: FSMContext):
    try:
        amount = Decimal((message.text or "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer("Введите сумму числом, например 270.")
        return
    if amount < Decimal("10") or amount > Decimal("500"):
        await message.answer("Сумма должна быть от 10 до 500 USDT-эквивалента.")
        return
    settings = p2p_settings()
    if not settings or not settings.get("usdt_kzt_rate"):
        await state.clear()
        await message.answer("Курс сейчас не задан. Попробуйте позже.")
        return
    rate = Decimal(str(settings["usdt_kzt_rate"]))
    fee_rate = Decimal(str(settings.get("fee_rate") or "0.04"))
    fee = (amount * fee_rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    direction = (await state.get_data()).get("p2p_direction")
    if direction == "usdt_to_kzt":
        target = ((amount - fee) * rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
        prompt = (
            f"Заявка: <b>{money(amount)} USDT → {money(target)} KZT</b>\n"
            f"Комиссия {money(fee)} USDT уже учтена.\n\n"
            "Введите номер карты и имя получателя, куда трейдер должен отправить тенге:"
        )
    else:
        target = amount - fee
        prompt = (
            f"Заявка: <b>{money(amount * rate)} KZT → {money(target)} USDT</b>\n"
            f"Комиссия {money(fee)} USDT уже учтена.\n\n"
            "Введите ваш адрес USDT TRC20 для получения:"
        )
    await state.update_data(p2p_amount=str(amount), p2p_rate=str(rate), p2p_fee=str(fee))
    await state.set_state(P2P.destination)
    await message.answer(prompt)


@dp.message(P2P.destination)
async def p2p_destination(message: Message, state: FSMContext):
    data = await state.get_data()
    direction = data.get("p2p_direction")
    destination = (message.text or "").strip()
    if len(destination) < 8 or len(destination) > 180:
        await message.answer("Проверьте реквизиты и отправьте их текстом ещё раз.")
        return
    if direction == "kzt_to_usdt" and not valid_tron_address(destination):
        await message.answer("Нужен корректный адрес USDT TRC20 (34 символа, начинается с T).")
        return
    user = ensure_user(message.from_user)
    if not user:
        await state.clear()
        await message.answer("Сначала зарегистрируйтесь.", reply_markup=register_kb())
        return
    if p2p_active_trade(user["id"]):
        await state.clear()
        await message.answer("У вас уже есть открытая P2P-заявка.")
        return
    trade_data = {
        "customer_user_id": user["id"],
        "direction": direction,
        "amount_usdt": data["p2p_amount"],
        "amount_kzt": str((Decimal(data["p2p_amount"]) * Decimal(data["p2p_rate"])).quantize(Decimal("1"), rounding=ROUND_DOWN)),
        "fee_usdt": data["p2p_fee"],
        "rate": data["p2p_rate"],
        "customer_destination": destination,
        "status": "new",
    }
    try:
        trade = db.table("p2p_trades").insert(trade_data).execute().data[0]
    except Exception:
        logging.exception("p2p trade create failed")
        await state.clear()
        await message.answer("Не удалось создать заявку. Попробуйте позже.")
        return

    traders = db.table("p2p_traders").select("id,user_id").eq("status", "active").execute().data or []
    delivered = 0
    for trader in traders:
        try:
            trader_user = db.table("bot_users").select("telegram_id,balance").eq("id", trader["user_id"]).limit(1).execute().data
            if not trader_user or Decimal(str(trader_user[0].get("balance") or 0)) < Decimal("250"):
                continue
            busy = db.table("p2p_trades").select("id", count="exact").eq("trader_id", trader["id"]).in_("status", ["accepted", "payment_reported", "payment_confirmed", "payout_sent"]).execute()
            used = db.table("p2p_traders").select("completed_volume_usdt,reserved_volume_usdt,max_volume_usdt").eq("id", trader["id"]).limit(1).execute().data
            if not used:
                continue
            row = used[0]
            free = Decimal(str(row["max_volume_usdt"])) - Decimal(str(row["completed_volume_usdt"])) - Decimal(str(row["reserved_volume_usdt"]))
            if free < Decimal(data["p2p_amount"]):
                continue
            builder = InlineKeyboardBuilder()
            builder.button(text=f"✅ Принять #{trade['trade_number']}", callback_data=f"p2p_accept:{trade['id']}")
            await bot.send_message(
                int(trader_user[0]["telegram_id"]),
                f"💱 <b>Новая P2P-заявка #{trade['trade_number']}</b>\n"
                f"Направление: <b>{'USDT → KZT' if direction == 'usdt_to_kzt' else 'KZT → USDT'}</b>\n"
                f"Сумма: <b>{money(data['p2p_amount'])} USDT-эквивалента</b>\n"
                f"Получатель запросит: <b>{money((Decimal(data['p2p_amount']) - Decimal(data['p2p_fee'])) * Decimal(data['p2p_rate']))} KZT</b> либо <b>{money(Decimal(data['p2p_amount']) - Decimal(data['p2p_fee']))} USDT</b>\n"
                "Реквизиты клиента откроются после принятия.",
                reply_markup=builder.as_markup(),
            )
            delivered += 1
        except Exception:
            logging.exception("p2p offer notify failed trade_id=%s", trade["id"])
    await state.clear()
    if not delivered:
        db.table("p2p_trades").update({"status": "cancelled", "cancelled_at": now(), "updated_at": now()}).eq("id", trade["id"]).execute()
        await message.answer("Сейчас нет трейдеров с подходящим лимитом. Заявка не создана; попробуйте позже.")
        return
    await message.answer(
        f"✅ Заявка #{trade['trade_number']} отправлена трейдерам.\n"
        "Переводите деньги только после того, как трейдер примет заявку. Бот не выполняет переводы автоматически."
    )


@dp.callback_query(F.data.startswith("p2p_accept:"))
async def p2p_accept(call: CallbackQuery):
    user = get_user(call.from_user.id)
    if not user:
        await call.answer("Сначала зарегистрируйтесь.", show_alert=True)
        return
    rows = db.table("p2p_traders").select("id,status").eq("user_id", user["id"]).limit(1).execute().data or []
    if not rows or rows[0]["status"] != "active":
        await call.answer("Только активный трейдер может принять заявку.", show_alert=True)
        return
    trade_id = call.data.split(":", 1)[1]
    try:
        result = db.rpc("accept_p2p_trade", {"p_trade_id": trade_id, "p_trader_id": rows[0]["id"]}).execute().data or {}
    except Exception:
        logging.exception("p2p accept failed trade_id=%s", trade_id)
        await call.answer("Не удалось принять заявку.", show_alert=True)
        return
    if not result.get("ok"):
        error = result.get("error", "unknown")
        labels = {"limit_reached": "Лимит 500 USDT уже занят.", "deposit_below_minimum": "Нужен подтверждённый депозит 250 USDT.", "trade_unavailable": "Заявку уже принял другой трейдер."}
        await call.answer(labels.get(error, "Заявка недоступна."), show_alert=True)
        return
    trade = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data[0]
    if trade["direction"] == "kzt_to_usdt":
        payment = (
            f"Переведите <b>{money(Decimal(trade['amount_kzt']))} KZT</b> на карту трейдера:\n"
            f"Банк: <b>{trade['trader_card_snapshot']['bank_name']}</b>\n"
            f"Получатель: <b>{trade['trader_card_snapshot']['card_holder']}</b>\n"
            f"Карта: <code>{trade['trader_card_snapshot']['card_requisites']}</code>"
        )
    else:
        payment = (
            f"Переведите <b>{money(trade['amount_usdt'])} USDT</b> в сети TRC20 на адрес трейдера:\n"
            f"<code>{trade['trader_usdt_address_snapshot']}</code>"
        )
    customer_rows = db.table("bot_users").select("telegram_id").eq("id", trade["customer_user_id"]).limit(1).execute().data or []
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🧾 Я оплатил / отправить чек", callback_data=f"p2p_pay:{trade_id}")
    keyboard.button(text="❌ Отменить до оплаты", callback_data=f"p2p_cancel:{trade_id}")
    keyboard.adjust(1)
    if customer_rows:
        await bot.send_message(
            int(customer_rows[0]["telegram_id"]),
            f"🤝 <b>Трейдер принял заявку #{trade['trade_number']}</b>\n\n{payment}\n\n"
            f"После проверки оплаты вы получите <b>{p2p_payout_text(trade)}</b> на указанные вами реквизиты. Сверьте данные перед переводом.",
            reply_markup=keyboard.as_markup(),
        )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Заявка принята. Клиент получил реквизиты.")


@dp.callback_query(F.data.startswith("p2p_pay:"))
async def p2p_pay(call: CallbackQuery, state: FSMContext):
    await call.answer()
    trade_id = call.data.split(":", 1)[1]
    user = get_user(call.from_user.id)
    trades = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data or []
    if not user or not trades or trades[0]["customer_user_id"] != user["id"] or trades[0]["status"] != "accepted":
        await call.message.answer("Эта заявка уже недоступна.")
        return
    await state.clear()
    await state.update_data(p2p_receipt_trade_id=trade_id)
    await state.set_state(P2P.receipt)
    await call.message.answer("Отправьте TXID текстом либо прикрепите фото/файл подтверждения перевода.")


@dp.message(P2P.receipt)
async def p2p_receipt(message: Message, state: FSMContext):
    trade_id = (await state.get_data()).get("p2p_receipt_trade_id")
    user = get_user(message.from_user.id)
    trades = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data or []
    if not user or not trades or trades[0]["customer_user_id"] != user["id"] or trades[0]["status"] != "accepted":
        await state.clear()
        await message.answer("Заявка не найдена или уже изменилась.")
        return
    update = {"status": "payment_reported", "payment_reported_at": now(), "updated_at": now()}
    proof = None
    if message.text:
        txid = message.text.strip()
        if len(txid) < 20:
            await message.answer("TXID слишком короткий. Отправьте полный TXID или фото/файл чека.")
            return
        update["payment_txid"] = txid
    elif message.photo:
        proof = ("photo", message.photo[-1].file_id)
        update.update({"payment_proof_kind": "photo", "payment_proof_file_id": proof[1]})
    elif message.document:
        proof = ("document", message.document.file_id)
        update.update({"payment_proof_kind": "document", "payment_proof_file_id": proof[1]})
    else:
        await message.answer("Отправьте TXID, фото или файл чека.")
        return
    changed = db.table("p2p_trades").update(update).eq("id", trade_id).eq("status", "accepted").select("id").execute().data or []
    if not changed:
        await state.clear()
        await message.answer("Статус заявки уже изменился.")
        return
    await state.clear()
    trade = trades[0]
    trader_user = db.table("p2p_traders").select("user_id").eq("id", trade["trader_id"]).limit(1).execute().data[0]
    trader_tg = db.table("bot_users").select("telegram_id").eq("id", trader_user["user_id"]).limit(1).execute().data[0]["telegram_id"]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Оплата поступила", callback_data=f"p2p_incoming:{trade_id}")
    kb.button(text="⚠️ Спор", callback_data=f"p2p_dispute:{trade_id}")
    kb.adjust(1)
    await bot.send_message(int(trader_tg), f"🧾 Клиент сообщил об оплате по заявке #{trade['trade_number']}. Проверьте фактическое поступление средств. Не подтверждайте по одному чеку.", reply_markup=kb.as_markup())
    await message.answer("✅ Подтверждение отправлено трейдеру. Бот не считает оплату полученной до ручной проверки трейдером.")


async def p2p_actor_trade(call: CallbackQuery, trade_id: str):
    user = get_user(call.from_user.id)
    if not user:
        return None, None
    rows = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data or []
    if not rows:
        return user, None
    trader = db.table("p2p_traders").select("id").eq("user_id", user["id"]).limit(1).execute().data or []
    if not trader or rows[0].get("trader_id") != trader[0]["id"]:
        return user, None
    return user, rows[0]


@dp.callback_query(F.data.startswith("p2p_incoming:"))
async def p2p_incoming(call: CallbackQuery):
    trade_id = call.data.split(":", 1)[1]
    _, trade = await p2p_actor_trade(call, trade_id)
    if not trade or trade["status"] != "payment_reported":
        await call.answer("Заявка недоступна.", show_alert=True)
        return
    changed = db.table("p2p_trades").update({"status": "payment_confirmed", "payment_confirmed_at": now(), "updated_at": now()}).eq("id", trade_id).eq("status", "payment_reported").select("id").execute().data or []
    if not changed:
        await call.answer("Статус уже изменился.", show_alert=True)
        return
    customer = db.table("bot_users").select("telegram_id").eq("id", trade["customer_user_id"]).limit(1).execute().data[0]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Выплата отправлена", callback_data=f"p2p_payout:{trade_id}")
    kb.button(text="⚠️ Спор", callback_data=f"p2p_dispute:{trade_id}")
    kb.adjust(1)
    await bot.send_message(int(customer["telegram_id"]), f"✅ Трейдер подтвердил получение оплаты по заявке #{trade['trade_number']}. Дождитесь ручной отправки {p2p_payout_text(trade)} трейдером.")
    await call.message.edit_text(f"✅ Поступление по заявке #{trade['trade_number']} отмечено. После ручного перевода клиенту нажмите кнопку ниже.")
    await call.message.answer("Когда вручную отправите выплату клиенту:", reply_markup=kb)
    await call.answer("Зафиксировано.")


@dp.callback_query(F.data.startswith("p2p_payout:"))
async def p2p_payout_sent(call: CallbackQuery):
    trade_id = call.data.split(":", 1)[1]
    _, trade = await p2p_actor_trade(call, trade_id)
    if not trade or trade["status"] != "payment_confirmed":
        await call.answer("Заявка недоступна.", show_alert=True)
        return
    changed = db.table("p2p_trades").update({"status": "payout_sent", "payout_sent_at": now(), "updated_at": now()}).eq("id", trade_id).eq("status", "payment_confirmed").select("id").execute().data or []
    if not changed:
        await call.answer("Статус уже изменился.", show_alert=True)
        return
    customer = db.table("bot_users").select("telegram_id").eq("id", trade["customer_user_id"]).limit(1).execute().data[0]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Деньги получил", callback_data=f"p2p_done:{trade_id}")
    kb.button(text="⚠️ Открыть спор", callback_data=f"p2p_dispute:{trade_id}")
    kb.adjust(1)
    await bot.send_message(int(customer["telegram_id"]), f"💸 Трейдер отметил ручную отправку выплаты по заявке #{trade['trade_number']}: {p2p_payout_text(trade)}. Проверьте поступление и подтвердите только после фактического получения.", reply_markup=kb.as_markup())
    await call.message.edit_text("💸 Выплата отмечена как отправленная; завершение ждёт подтверждения клиента.")
    await call.answer("Готово.")


@dp.callback_query(F.data.startswith("p2p_done:"))
async def p2p_customer_done(call: CallbackQuery):
    trade_id = call.data.split(":", 1)[1]
    user = get_user(call.from_user.id)
    trades = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data or []
    if not user or not trades or trades[0]["customer_user_id"] != user["id"] or trades[0]["status"] != "payout_sent":
        await call.answer("Заявка недоступна.", show_alert=True)
        return
    try:
        result = db.rpc("finish_p2p_trade", {"p_trade_id": trade_id, "p_trader_id": trades[0]["trader_id"]}).execute().data or {}
    except Exception:
        logging.exception("p2p completion failed trade_id=%s", trade_id)
        await call.answer("Не удалось завершить заявку.", show_alert=True)
        return
    if not result.get("ok"):
        await call.answer("Статус уже изменился.", show_alert=True)
        return
    trader = db.table("p2p_traders").select("user_id").eq("id", trades[0]["trader_id"]).limit(1).execute().data[0]
    trader_tg = db.table("bot_users").select("telegram_id").eq("id", trader["user_id"]).limit(1).execute().data[0]["telegram_id"]
    await notify_user(int(trader_tg), f"✅ Клиент подтвердил получение. Сделка #{trades[0]['trade_number']} закрыта.")
    await call.message.edit_text(f"✅ Сделка #{trades[0]['trade_number']} завершена. Оборот учтён в лимите трейдера 500 USDT.")
    await call.answer("Спасибо за подтверждение.")


@dp.callback_query(F.data.startswith("p2p_cancel:"))
async def p2p_cancel(call: CallbackQuery):
    trade_id = call.data.split(":", 1)[1]
    user, _ = await p2p_actor_trade(call, trade_id)
    trade_rows = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data or []
    if not trade_rows:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    trade = trade_rows[0]
    is_customer = bool(user and user["id"] == trade["customer_user_id"])
    if not is_customer and not (user and trade.get("trader_id")):
        await call.answer("Нет доступа.", show_alert=True)
        return
    try:
        result = db.rpc("cancel_p2p_trade", {"p_trade_id": trade_id, "p_actor_telegram_id": call.from_user.id}).execute().data or {}
    except Exception:
        logging.exception("p2p cancellation failed trade_id=%s", trade_id)
        await call.answer("Не удалось отменить.", show_alert=True)
        return
    if not result.get("ok"):
        await call.answer("Эту заявку уже нельзя отменить; откройте спор.", show_alert=True)
        return
    await call.message.edit_text(f"❌ Сделка #{trade['trade_number']} отменена до подтверждения оплаты.")
    await call.answer("Отменено.")


@dp.callback_query(F.data.startswith("p2p_dispute:"))
async def p2p_dispute(call: CallbackQuery):
    trade_id = call.data.split(":", 1)[1]
    user = get_user(call.from_user.id)
    trades = db.table("p2p_trades").select("*").eq("id", trade_id).limit(1).execute().data or []
    if not user or not trades:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    trade = trades[0]
    is_customer = trade["customer_user_id"] == user["id"]
    is_trader = False
    if trade.get("trader_id"):
        own = db.table("p2p_traders").select("user_id").eq("id", trade["trader_id"]).limit(1).execute().data or []
        is_trader = bool(own and own[0]["user_id"] == user["id"])
    if not is_customer and not is_trader:
        await call.answer("Нет доступа.", show_alert=True)
        return
    changed = db.table("p2p_trades").update({"status": "disputed", "updated_at": now()}).eq("id", trade_id).in_("status", ["accepted", "payment_reported", "payment_confirmed", "payout_sent"]).select("id").execute().data or []
    if not changed:
        await call.answer("Спор нельзя открыть для этой заявки.", show_alert=True)
        return
    customer = db.table("bot_users").select("telegram_id").eq("id", trade["customer_user_id"]).limit(1).execute().data[0]
    trader_user = db.table("p2p_traders").select("user_id").eq("id", trade["trader_id"]).limit(1).execute().data[0]
    trader_tg = db.table("bot_users").select("telegram_id").eq("id", trader_user["user_id"]).limit(1).execute().data[0]["telegram_id"]
    for aid in admin_ids():
        await notify_user(aid, f"⚠️ <b>P2P спор #{trade['trade_number']}</b>\nКлиент Telegram ID: <code>{customer['telegram_id']}</code>\nТрейдер Telegram ID: <code>{trader_tg}</code>\nСтатус перед спором: {trade['status']}\nПроверьте перевод вручную.")
    await call.message.answer("⚠️ Спор зарегистрирован и отправлен администраторам. Резерв лимита не снимался.")
    await call.answer("Спор открыт.")


@dp.callback_query(F.data == "p2p:trader_setup")
async def p2p_trader_setup_callback(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await begin_trader_setup(call.message, call.from_user, state)


@dp.message(Command("trader"))
async def trader_setup_command(message: Message, state: FSMContext):
    await begin_trader_setup(message, message.from_user, state)


@dp.message(TraderSetup.bank_name)
async def trader_bank_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 2 or len(value) > 80:
        await message.answer("Введите название банка длиной 2–80 символов.")
        return
    await state.update_data(trader_bank_name=value)
    await state.set_state(TraderSetup.card_holder)
    await message.answer("Введите ФИО владельца банковской карты:")


@dp.message(TraderSetup.card_holder)
async def trader_card_holder(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 3 or len(value) > 100:
        await message.answer("Введите ФИО длиной 3–100 символов.")
        return
    await state.update_data(trader_card_holder=value)
    await state.set_state(TraderSetup.card_requisites)
    await message.answer("Введите номер карты или безопасные банковские реквизиты для получения KZT:")


@dp.message(TraderSetup.card_requisites)
async def trader_card_requisites(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 8 or len(value) > 100:
        await message.answer("Проверьте реквизиты карты и отправьте их ещё раз.")
        return
    await state.update_data(trader_card_requisites=value)
    await state.set_state(TraderSetup.usdt_address)
    await message.answer("Введите адрес кошелька трейдера USDT TRC20 для приёма USDT:")


@dp.message(TraderSetup.usdt_address)
async def trader_usdt_address(message: Message, state: FSMContext):
    address = (message.text or "").strip()
    if not valid_tron_address(address):
        await message.answer("Нужен корректный адрес USDT TRC20 (34 символа, начинается с T).")
        return
    user = ensure_user(message.from_user)
    if not user or Decimal(str(user.get("balance") or 0)) < Decimal("250"):
        await state.clear()
        await message.answer("Статус трейдера не активирован: подтверждённый рабочий депозит должен быть не меньше 250 USDT.")
        return
    data = await state.get_data()
    update = {
        "status": "active",
        "bank_name": data["trader_bank_name"],
        "card_holder": data["trader_card_holder"],
        "card_requisites": data["trader_card_requisites"],
        "usdt_trc20_address": address,
        "updated_at": now(),
    }
    try:
        db.table("p2p_traders").update(update).eq("user_id", user["id"]).execute()
    except Exception:
        logging.exception("trader setup save failed telegram_id=%s", message.from_user.id)
        await state.clear()
        await message.answer("Не удалось сохранить реквизиты. Попробуйте /trader позже.")
        return
    await state.clear()
    await message.answer("✅ Профиль трейдера активен. Клиентам будут показаны ваши реквизиты только после принятия вашей заявки; лимит оборота — 500 USDT.")


@dp.message(Command("setrate"))
async def set_p2p_rate(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Укажите курс командой /setrate 500 — тенге за 1 USDT.")
        return
    try:
        rate = Decimal(parts[1].replace(",", ".")).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError):
        await message.answer("Курс должен быть числом больше нуля.")
        return
    if rate <= 0 or rate > Decimal("1000000"):
        await message.answer("Курс должен быть больше нуля и не выше 1 000 000.")
        return
    try:
        db.table("p2p_settings").update({"usdt_kzt_rate": str(rate), "updated_by": message.from_user.id, "updated_at": now()}).eq("id", 1).execute()
        await message.answer(f"✅ P2P-курс сохранён: 1 USDT = {money(rate)} KZT. Комиссия — 4%.")
    except Exception:
        logging.exception("p2p rate update failed admin_id=%s", message.from_user.id)
        await message.answer("Не удалось сохранить курс.")


@dp.message(Command("p2pstatus"))
async def p2p_status_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    settings = p2p_settings()
    if not settings or not settings.get("usdt_kzt_rate"):
        await message.answer("P2P закрыт: курс ещё не задан. Используйте /setrate <тенге за USDT>.")
        return
    await message.answer(f"P2P активен. Курс: 1 USDT = {money(settings['usdt_kzt_rate'])} KZT; комиссия: {Decimal(str(settings['fee_rate'])) * 100}%.")


if __name__ == "__main__":
    asyncio.run(main())
