import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError("BOT_TOKEN, SUPABASE_URL and SUPABASE_SECRET_KEY are required")

db: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


class Finance(StatesGroup):
    amount = State()
    description = State()


class ClientFlow(StatesGroup):
    name = State()
    phone = State()
    note = State()


class TaskFlow(StatesGroup):
    title = State()
    due = State()


def money(v):
    return f"{Decimal(str(v or 0)):,.0f}".replace(",", " ")


def now():
    return datetime.now(timezone.utc).isoformat()


def kb(rows):
    b = InlineKeyboardBuilder()
    for text, data in rows:
        b.button(text=text, callback_data=data)
    b.adjust(2)
    return b.as_markup()


def main_kb():
    return kb([
        ("💰 Финансы", "menu:finance"),
        ("👥 Клиенты", "menu:clients"),
        ("📋 Задачи", "menu:tasks"),
        ("📊 Аналитика", "menu:analytics"),
        ("🤖 AI-помощник", "menu:ai"),
        ("⚙️ Настройки", "menu:settings"),
    ])


def back_kb():
    return kb([("⬅️ Главное меню", "menu:home")])


def get_user(tg_id):
    r = db.table("business_users").select("*").eq("telegram_id", tg_id).limit(1).execute()
    return r.data[0] if r.data else None


def ensure_user(tg):
    existing = get_user(tg.id)
    data = {
        "telegram_id": tg.id,
        "username": tg.username,
        "first_name": tg.first_name,
        "last_name": tg.last_name,
        "updated_at": now(),
    }
    if existing:
        db.table("business_users").update(data).eq("telegram_id", tg.id).execute()
        existing.update(data)
        return existing
    data["created_at"] = now()
    return db.table("business_users").insert(data).execute().data[0]


def home_text(user):
    name = user.get("first_name") or "предприниматель"
    uid = user["id"]
    income = db.table("transactions").select("amount").eq("user_id", uid).eq("type", "income").execute().data or []
    expense = db.table("transactions").select("amount").eq("user_id", uid).eq("type", "expense").execute().data or []
    total_income = sum(Decimal(str(x["amount"])) for x in income)
    total_expense = sum(Decimal(str(x["amount"])) for x in expense)
    clients = len(db.table("clients").select("id").eq("user_id", uid).execute().data or [])
    tasks = len(db.table("tasks").select("id").eq("user_id", uid).eq("status", "open").execute().data or [])
    return (
        f"👋 <b>БИЗНЕС БОТ</b>\n"
        f"<i>Твой бизнес — прямо в Telegram</i>\n\n"
        f"Привет, <b>{name}</b>!\n\n"
        f"💰 Доход: <b>{money(total_income)} ₸</b>\n"
        f"💸 Расход: <b>{money(total_expense)} ₸</b>\n"
        f"📈 Прибыль: <b>{money(total_income-total_expense)} ₸</b>\n"
        f"👥 Клиентов: <b>{clients}</b>\n"
        f"📋 Открытых задач: <b>{tasks}</b>\n\n"
        f"Выбери раздел:"
    )


async def show_home(target, tg):
    u = ensure_user(tg)
    text = home_text(u)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=main_kb())
        await target.answer()
    else:
        await target.answer(text, reply_markup=main_kb())


@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await show_home(message, message.from_user)


@dp.callback_query(F.data == "menu:home")
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(call, call.from_user)


@dp.callback_query(F.data == "menu:finance")
async def finance(call: CallbackQuery):
    u = ensure_user(call.from_user)
    rows = db.table("transactions").select("*").eq("user_id", u["id"]).order("created_at", desc=True).limit(8).execute().data or []
    income = sum(Decimal(str(x["amount"])) for x in rows if x["type"] == "income")
    expense = sum(Decimal(str(x["amount"])) for x in rows if x["type"] == "expense")
    text = (
        f"💰 <b>Финансы</b>\n\n"
        f"Последние операции:\n"
        f"➕ {money(income)} ₸ доходов\n"
        f"➖ {money(expense)} ₸ расходов\n\n"
    )
    if rows:
        for x in rows:
            sign = "+" if x["type"] == "income" else "−"
            text += f"{sign} <b>{money(x['amount'])} ₸</b> — {x.get('description') or 'Без описания'}\n"
    else:
        text += "Операций пока нет.\n"
    await call.message.edit_text(text, reply_markup=kb([
        ("➕ Доход", "finance:income"),
        ("➖ Расход", "finance:expense"),
        ("📜 Все операции", "finance:history"),
        ("⬅️ Главное меню", "menu:home"),
    ]))
    await call.answer()


@dp.callback_query(F.data.in_({"finance:income", "finance:expense"}))
async def finance_start(call: CallbackQuery, state: FSMContext):
    kind = "income" if call.data.endswith("income") else "expense"
    await state.clear()
    await state.set_state(Finance.amount)
    await state.update_data(type=kind)
    label = "доход" if kind == "income" else "расход"
    await call.message.edit_text(
        f"{'➕' if kind == 'income' else '➖'} <b>Добавить {label}</b>\n\n"
        f"Введите сумму в тенге. Например: <code>50000</code>",
        reply_markup=back_kb()
    )
    await call.answer()


@dp.message(Finance.amount)
async def finance_amount(message: Message, state: FSMContext):
    raw = (message.text or "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("❌ Введите корректную сумму, например <code>50000</code>.")
        return
    await state.update_data(amount=float(amount))
    await state.set_state(Finance.description)
    await message.answer("📝 Напишите описание операции. Например: <i>съёмка Reels для клиента</i>.", reply_markup=back_kb())


@dp.message(Finance.description)
async def finance_description(message: Message, state: FSMContext):
    data = await state.get_data()
    u = ensure_user(message.from_user)
    db.table("transactions").insert({
        "user_id": u["id"],
        "type": data["type"],
        "amount": data["amount"],
        "description": (message.text or "")[:500],
        "created_at": now(),
    }).execute()
    await state.clear()
    await message.answer(
        f"✅ Операция сохранена.\n\n"
        f"{'Доход' if data['type']=='income' else 'Расход'}: <b>{money(data['amount'])} ₸</b>\n"
        f"Описание: {message.text or '—'}",
        reply_markup=main_kb()
    )


@dp.callback_query(F.data == "finance:history")
async def finance_history(call: CallbackQuery):
    u = ensure_user(call.from_user)
    rows = db.table("transactions").select("*").eq("user_id", u["id"]).order("created_at", desc=True).limit(30).execute().data or []
    if not rows:
        text = "📜 <b>История</b>\n\nОпераций пока нет."
    else:
        lines = ["📜 <b>История операций</b>", ""]
        for x in rows:
            sign = "+" if x["type"] == "income" else "−"
            date = str(x["created_at"]).replace("T", " ")[:16]
            lines.append(f"<code>{date}</code> {sign}<b>{money(x['amount'])} ₸</b> — {x.get('description') or '—'}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()


@dp.callback_query(F.data == "menu:clients")
async def clients(call: CallbackQuery):
    u = ensure_user(call.from_user)
    rows = db.table("clients").select("*").eq("user_id", u["id"]).order("created_at", desc=True).limit(15).execute().data or []
    text = "👥 <b>Клиенты</b>\n\n"
    if not rows:
        text += "Клиентов пока нет."
    else:
        for x in rows:
            text += f"• <b>{x['name']}</b>"
            if x.get("phone"):
                text += f" — {x['phone']}"
            text += "\n"
    await call.message.edit_text(text, reply_markup=kb([
        ("➕ Добавить клиента", "client:add"),
        ("⬅️ Главное меню", "menu:home"),
    ]))
    await call.answer()


@dp.callback_query(F.data == "client:add")
async def client_add(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ClientFlow.name)
    await call.message.edit_text("👤 <b>Новый клиент</b>\n\nВведите имя или название компании.", reply_markup=back_kb())
    await call.answer()


@dp.message(ClientFlow.name)
async def client_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "")[:200])
    await state.set_state(ClientFlow.phone)
    await message.answer("📱 Телефон или @username клиента. Можно написать «пропустить».", reply_markup=back_kb())


@dp.message(ClientFlow.phone)
async def client_phone(message: Message, state: FSMContext):
    await state.update_data(phone=(message.text or "")[:100])
    await state.set_state(ClientFlow.note)
    await message.answer("📝 Заметка о клиенте или «пропустить».", reply_markup=back_kb())


@dp.message(ClientFlow.note)
async def client_note(message: Message, state: FSMContext):
    data = await state.get_data()
    u = ensure_user(message.from_user)
    db.table("clients").insert({
        "user_id": u["id"],
        "name": data["name"],
        "phone": None if (message.text or "").lower() == "пропустить" else data["phone"],
        "note": None if (message.text or "").lower() == "пропустить" else (message.text or "")[:500],
        "created_at": now(),
    }).execute()
    await state.clear()
    await message.answer(f"✅ Клиент <b>{data['name']}</b> добавлен.", reply_markup=main_kb())


@dp.callback_query(F.data == "menu:tasks")
async def tasks(call: CallbackQuery):
    u = ensure_user(call.from_user)
    rows = db.table("tasks").select("*").eq("user_id", u["id"]).eq("status", "open").order("created_at", desc=True).limit(20).execute().data or []
    text = "📋 <b>Задачи</b>\n\n"
    if not rows:
        text += "Открытых задач нет."
    else:
        for x in rows:
            due = f" · {x['due_date']}" if x.get("due_date") else ""
            text += f"• {x['title']}{due}\n"
    await call.message.edit_text(text, reply_markup=kb([
        ("➕ Добавить задачу", "task:add"),
        ("⬅️ Главное меню", "menu:home"),
    ]))
    await call.answer()


@dp.callback_query(F.data == "task:add")
async def task_add(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TaskFlow.title)
    await call.message.edit_text("📋 <b>Новая задача</b>\n\nНапишите, что нужно сделать.", reply_markup=back_kb())
    await call.answer()


@dp.message(TaskFlow.title)
async def task_title(message: Message, state: FSMContext):
    await state.update_data(title=(message.text or "")[:300])
    await state.set_state(TaskFlow.due)
    await message.answer("📅 Дедлайн: YYYY-MM-DD или «пропустить».", reply_markup=back_kb())


@dp.message(TaskFlow.due)
async def task_due(message: Message, state: FSMContext):
    due = (message.text or "").strip()
    if due.lower() == "пропустить":
        due = None
    else:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            await message.answer("❌ Формат: <code>2026-10-01</code> или напишите «пропустить».")
            return
    data = await state.get_data()
    u = ensure_user(message.from_user)
    db.table("tasks").insert({
        "user_id": u["id"],
        "title": data["title"],
        "due_date": due,
        "status": "open",
        "created_at": now(),
    }).execute()
    await state.clear()
    await message.answer("✅ Задача добавлена.", reply_markup=main_kb())


@dp.callback_query(F.data == "menu:analytics")
async def analytics(call: CallbackQuery):
    u = ensure_user(call.from_user)
    tx = db.table("transactions").select("*").eq("user_id", u["id"]).execute().data or []
    inc = sum(Decimal(str(x["amount"])) for x in tx if x["type"] == "income")
    exp = sum(Decimal(str(x["amount"])) for x in tx if x["type"] == "expense")
    clients_count = len(db.table("clients").select("id").eq("user_id", u["id"]).execute().data or [])
    open_tasks = len(db.table("tasks").select("id").eq("user_id", u["id"]).eq("status", "open").execute().data or [])
    text = (
        f"📊 <b>Аналитика</b>\n\n"
        f"💰 Доход: <b>{money(inc)} ₸</b>\n"
        f"💸 Расход: <b>{money(exp)} ₸</b>\n"
        f"📈 Прибыль: <b>{money(inc-exp)} ₸</b>\n\n"
        f"👥 Клиентов: <b>{clients_count}</b>\n"
        f"📋 Открытых задач: <b>{open_tasks}</b>"
    )
    await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()


@dp.callback_query(F.data == "menu:ai")
async def ai(call: CallbackQuery):
    await call.message.edit_text(
        "🤖 <b>AI-помощник</b>\n\n"
        "Раздел подготовлен для следующего этапа. Здесь можно будет писать обычным языком:\n\n"
        "• «Сколько я заработал?»\n"
        "• «Создай КП для стоматологии»\n"
        "• «Придумай 5 идей Reels»\n"
        "• «Напомни позвонить клиенту завтра»",
        reply_markup=back_kb()
    )
    await call.answer()


@dp.callback_query(F.data == "menu:settings")
async def settings(call: CallbackQuery):
    await call.message.edit_text(
        "⚙️ <b>Настройки</b>\n\n"
        "БИЗНЕС БОТ сохраняет данные вашего рабочего кабинета в защищённой базе.",
        reply_markup=back_kb()
    )
    await call.answer()


@dp.callback_query(F.data == "menu:home")
async def duplicate_home(call: CallbackQuery, state: FSMContext):
    # Kept intentionally as a single route guard for old Telegram clients.
    await state.clear()
    await show_home(call, call.from_user)


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
