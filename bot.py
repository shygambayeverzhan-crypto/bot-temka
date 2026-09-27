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

MIN_DEPOSIT = Decimal("250")
THRESHOLD = Decimal("500")
FEE_RATE = Decimal("0.04")
ADMIN_TRC20_ADDRESS = os.getenv("ADMIN_TRC20_ADDRESS", "TNTuGJ2LrGR9va5ywJq8zAx8CxjVUJRMzP").strip()

class Deposit(StatesGroup):
    amount = State()
    txid = State()

class Withdrawal(StatesGroup):
    address = State()

def register_kb():
    b = InlineKeyboardBuilder(); b.button(text="📝 Регистрация", callback_data="register"); return b.as_markup()

def home_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💰 Внести депозит", callback_data="menu:deposit")
    b.button(text="💳 Баланс", callback_data="menu:balance")
    b.button(text="📤 Вывести средства", callback_data="menu:withdraw")
    b.button(text="📜 История", callback_data="menu:history")
    b.button(text="ℹ️ Помощь", callback_data="menu:help")
    b.adjust(1,2,2,1); return b.as_markup()

def back_kb():
    b=InlineKeyboardBuilder(); b.button(text="⬅️ Главное меню",callback_data="menu:home"); return b.as_markup()

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
    await message.answer(f"🧾 <b>Заявка #{o['order_number']}</b>\n\nСумма: <b>{money(amount)} USDT</b>\nСеть: <b>TRC20</b>\n\nАдрес для оплаты:\n<code>{ADMIN_TRC20_ADDRESS}</code>\n\nПосле перевода отправьте <b>TXID</b>.",reply_markup=back_kb())

@dp.message(Deposit.txid)
async def deposit_txid(message:Message,state:FSMContext):
    oid=(await state.get_data()).get("order_id")
    if not oid: await state.clear(); return
    rows=db.table("orders").select("*").eq("id",oid).limit(1).execute().data
    if not rows: await state.clear(); await message.answer("Заявка не найдена.",reply_markup=home_kb()); return
    txid=(message.text or "").strip()
    if len(txid)<20: await message.answer("❌ Отправьте корректный TXID TRC20."); return
    db.table("orders").update({"deposit_tx_hash":txid,"status":"under_review","updated_at":now()}).eq("id",oid).execute()
    await state.clear(); await message.answer("✅ <b>TXID получен.</b>\n\nЗаявка передана администратору на проверку.",reply_markup=home_kb()); await notify_admins(oid)

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

if __name__ == "__main__":
    asyncio.run(main())

async def notify_admins(order_id):
    rows = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not rows:
        return
    o = rows[0]
    urows = db.table("bot_users").select("*").eq("id", o["user_id"]).limit(1).execute().data
    u = urows[0] if urows else {}
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"confirm:{order_id}")
    b.button(text="❌ Отклонить", callback_data=f"reject:{order_id}")
    b.adjust(2)
    text_msg = (
        f"🔔 <b>Депозит #{o['order_number']}</b>\n\n"
        f"Клиент: @{u.get('username') or 'без_username'}\n"
        f"Сумма: <b>{money(o['amount'])} USDT</b>\n"
        f"Сеть: <b>TRC20</b>\n"
        f"TXID: <code>{o.get('deposit_tx_hash') or '—'}</code>"
    )
    for aid in admin_ids():
        await bot.send_message(aid, text_msg, reply_markup=b.as_markup())

if __name__ == "__main__":
    asyncio.run(main())
