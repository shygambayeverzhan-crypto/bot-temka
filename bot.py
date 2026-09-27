import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
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

class Auth(StatesGroup):
    login = State()
    password = State()


LANGUAGE_OPTIONS = [
    ("ru","🇷🇺 Русский"),("en","🇺🇸 English"),("uk","🇺🇦 Українська"),("kk","🇰🇿 Қазақша"),
    ("pl","🇵🇱 Polski"),("ro","🇲🇩 Română"),("tr","🇹🇷 Türkçe"),("es","🇪🇸 Español"),
    ("de","🇩🇪 Deutsch"),("ky","🇰🇬 Кыргызча"),("ka","🇬🇪 ქართული"),("zh","🇨🇳 中文"),
    ("ko","🇰🇷 한국어"),("ar","🇸🇦 العربية"),("ja","🇯🇵 日本語"),("fr","🇫🇷 Français"),
    ("pt","🇵🇹 Português"),("nl","🇳🇱 Nederlands"),("hi","🇮🇳 हिन्दी"),("sk","🇸🇰 Slovenčina"),
]
# choose, welcome, unauthorized, prompt, login, change language, documentation
AUTH_COPY = {
"ru":("🇷🇺 Выберите язык 🇷🇺","👋 Добро пожаловать!","❌ Вы не авторизованы.","Введите логин и пароль для входа:","Вход","Сменить язык","Документация"),
"en":("🇺🇸 Choose your language 🇺🇸","👋 Welcome!","❌ You are not authorized.","Enter your login and password to sign in:","Login","Change language","Documentation"),
"uk":("🇺🇦 Виберіть мову 🇺🇦","👋 Ласкаво просимо!","❌ Ви не авторизовані.","Введіть логін і пароль для входу:","Увійти","Змінити мову","Документація"),
"kk":("🇰🇿 Тілді таңдаңыз 🇰🇿","👋 Қош келдіңіз!","❌ Сіз авторизацияланбағансыз.","Кіру үшін логин мен парольді енгізіңіз:","Кіру","Тілді өзгерту","Құжаттама"),
"pl":("🇵🇱 Wybierz język 🇵🇱","👋 Witamy!","❌ Nie jesteś zalogowany.","Wpisz login i hasło, aby się zalogować:","Zaloguj się","Zmień język","Dokumentacja"),
"ro":("🇷🇴 Selectați limba 🇷🇴","👋 Bine ați venit!","❌ Nu sunteți autentificat.","Introduceți loginul și parola:","Autentificare","Schimbați limba","Documentație"),
"tr":("🇹🇷 Dil seçin 🇹🇷","👋 Hoş geldiniz!","❌ Yetkilendirilmediniz.","Giriş için kullanıcı adınızı ve parolanızı girin:","Giriş yap","Dili değiştir","Belgeler"),
"es":("🇪🇸 Elige tu idioma 🇪🇸","👋 ¡Bienvenido!","❌ No tienes autorización.","Introduce tu usuario y contraseña:","Iniciar sesión","Cambiar idioma","Documentación"),
"de":("🇩🇪 Wählen Sie Ihre Sprache 🇩🇪","👋 Willkommen!","❌ Sie sind nicht angemeldet.","Geben Sie Login und Passwort ein:","Anmelden","Sprache ändern","Dokumentation"),
"ky":("🇰🇬 Тилди тандаңыз 🇰🇬","👋 Кош келиңиз!","❌ Сиз авторизациядан өткөн жоксуз.","Кирүү үчүн логин менен сырсөздү киргизиңиз:","Кирүү","Тилди өзгөртүү","Документация"),
"ka":("🇬🇪 აირჩიეთ ენა 🇬🇪","👋 კეთილი იყოს თქვენი მობრძანება!","❌ ავტორიზებული არ ხართ.","შესასვლელად შეიყვანეთ ლოგინი და პაროლი:","შესვლა","ენის შეცვლა","დოკუმენტაცია"),
"zh":("🇨🇳 请选择您的语言 🇨🇳","👋 欢迎！","❌ 您尚未获得授权。","请输入登录名和密码：","登录","更改语言","文档"),
"ko":("🇰🇷 언어를 선택하세요 🇰🇷","👋 환영합니다!","❌ 인증되지 않았습니다.","아이디와 비밀번호를 입력하세요:","로그인","언어 변경","문서"),
"ar":("🇸🇦 اختر لغتك 🇸🇦","👋 مرحبًا!","❌ لم يتم تفويضك.","أدخل اسم المستخدم وكلمة المرور:","تسجيل الدخول","تغيير اللغة","التوثيق"),
"ja":("🇯🇵 言語を選択 🇯🇵","👋 ようこそ！","❌ 認証されていません。","ユーザー名とパスワードを入力してください:","ログイン","言語を変更","ドキュメント"),
"fr":("🇫🇷 Choisissez votre langue 🇫🇷","👋 Bienvenue !","❌ Vous n’êtes pas autorisé.","Saisissez votre identifiant et votre mot de passe :","Se connecter","Changer de langue","Documentation"),
"pt":("🇵🇹 Escolha seu idioma 🇵🇹","👋 Bem-vindo!","❌ Você não está autorizado.","Digite seu login e senha:","Entrar","Mudar idioma","Documentação"),
"nl":("🇳🇱 Kies uw taal 🇳🇱","👋 Welkom!","❌ U bent niet geautoriseerd.","Voer uw login en wachtwoord in:","Inloggen","Taal wijzigen","Documentatie"),
"hi":("🇮🇳 अपनी भाषा चुनें 🇮🇳","👋 स्वागत है!","❌ आप अधिकृत नहीं हैं।","लॉगिन के लिए यूज़रनेम और पासवर्ड दर्ज करें:","लॉगिन","भाषा बदलें","दस्तावेज़"),
"sk":("🇸🇰 Vyberte jazyk 🇸🇰","👋 Vitajte!","❌ Nie ste autorizovaný.","Zadajte prihlasovacie meno a heslo:","Prihlásiť sa","Zmeniť jazyk","Dokumentácia"),
}
AUTH_KEYS = ("choose","welcome","unauth","prompt","login","change","docs")
AUTH_PROMPTS = {
"ru":{"login":"Введите ваш логин:","password":"Введите пароль. Сообщение с паролем будет удалено после проверки.","failed":"Неверный логин или пароль. Попробуйте ещё раз.","success":"✅ Вход выполнен.","docs":"Доступ выдаёт администратор. Не пересылайте пароль. Сообщение с паролем удаляется после проверки."},
"kk":{"login":"Логиніңізді енгізіңіз:","password":"Парольді енгізіңіз. Тексеруден кейін хабарлама жойылады.","failed":"Логин немесе пароль қате. Қайталап көріңіз.","success":"✅ Сіз жүйеге кірдіңіз.","docs":"Қол жеткізу деректерін әкімші береді. Парольді басқа адамдарға жібермеңіз."},
"en":{"login":"Enter your login:","password":"Enter your password. This message will be deleted after verification.","failed":"Incorrect login or password. Please try again.","success":"✅ You are signed in.","docs":"Access credentials are issued by an administrator. Do not share your password. The password message is deleted after verification."},
}
def locale_for(telegram_id):
    rows = db.table("bot_users").select("language").eq("telegram_id", telegram_id).limit(1).execute().data or []
    code = rows[0].get("language") if rows else "ru"
    return code if code in AUTH_COPY else "ru"


def ui(code,key):
    values=AUTH_COPY.get(code,AUTH_COPY["ru"])
    return values[AUTH_KEYS.index(key)]
def auth_prompt(code,key):
    return AUTH_PROMPTS.get(code,AUTH_PROMPTS["en"])[key]
def language_picker_kb():
    b=InlineKeyboardBuilder()
    for code,label in LANGUAGE_OPTIONS: b.button(text=label,callback_data=f"lang:{code}")
    b.adjust(2)
    return b.as_markup()
def auth_kb(code="ru"):
    b=InlineKeyboardBuilder()
    b.button(text=f"🔐 {ui(code,'login')}",callback_data="auth:login")
    b.button(text=f"🌐 {ui(code,'change')}",callback_data="auth:language")
    b.button(text=f"📄 {ui(code,'docs')}",callback_data="auth:docs")
    b.adjust(1)
    return b.as_markup()
def auth_screen(code="ru"):
    return f"{ui(code,'welcome')}\n{ui(code,'unauth')}\n\n{ui(code,'prompt')}"
def docs_screen(code="ru"):
    return f"📄 <b>{ui(code,'docs')}</b>\n\n{auth_prompt(code,'docs')}"
def ensure_identity(tg):
    row=get_user(tg.id)
    if row:return row
    data={"telegram_id":tg.id,"username":tg.username,"first_name":tg.first_name,"last_name":tg.last_name,"language":"ru","language_selected":False}
    try: db.table("bot_users").insert(data).execute()
    except Exception: logging.exception("create identity row failed telegram_id=%s",tg.id)
    return get_user(tg.id)
def password_hash(password,salt):
    return hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),salt,600_000,dklen=32).hex()


def register_kb():
    return auth_kb("ru")

def home_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💰 Внести депозит", callback_data="menu:deposit")
    b.button(text="💳 Баланс", callback_data="menu:balance")
    b.button(text="📤 Вывести средства", callback_data="menu:withdraw")
    b.button(text="📜 История", callback_data="menu:history")
    b.button(text="ℹ️ Помощь", callback_data="menu:help")
    b.button(text="🌐 Язык", callback_data="menu:language")
    b.adjust(1,2,2,1,1); return b.as_markup()

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
    row=ensure_identity(tg)
    code=(row or {}).get("language") or "ru"
    if not (row or {}).get("language_selected"):
        text=ui(code,"choose"); markup=language_picker_kb()
    else:
        u=ensure_user(tg)
        if not u: text=auth_screen(code); markup=auth_kb(code)
        elif u.get("is_blocked"): text="⛔ Доступ ограничен."; markup=None
        else: text=home_text(u); markup=home_kb()
    if isinstance(target,CallbackQuery):
        await target.message.edit_text(text,reply_markup=markup); await target.answer()
    else:
        await target.answer(text,reply_markup=markup)

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

@dp.callback_query(F.data.in_({"menu:language", "auth:language"}))
async def menu_language(call: CallbackQuery):
    await call.answer()
    code=locale_for(call.from_user.id)
    await call.message.edit_text(ui(code,"choose"),reply_markup=language_picker_kb())

@dp.callback_query(F.data == "menu:deposit")
async def menu_deposit(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await start_deposit_flow(call.message, state, call.from_user)

@dp.message(Command("start"))
async def start(message: Message,state:FSMContext):
    await state.clear()
    row=ensure_identity(message.from_user)
    code=(row or {}).get("language") or "ru"
    if not (row or {}).get("language_selected"):
        await message.answer(ui(code,"choose"),reply_markup=language_picker_kb()); return
    user=ensure_user(message.from_user)
    if not user:
        await message.answer(auth_screen(code),reply_markup=auth_kb(code)); return
    if user.get("is_blocked"):
        await message.answer("⛔ Доступ ограничен."); return
    await message.answer(home_text(user),reply_markup=home_kb())

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





@dp.callback_query(F.data.startswith("lang:"))
async def choose_language(call:CallbackQuery,state:FSMContext):
    code=call.data.split(":",1)[1]
    if code not in AUTH_COPY:
        await call.answer("Unknown language",show_alert=True); return
    ensure_identity(call.from_user)
    db.table("bot_users").update({"language":code,"language_selected":True,"updated_at":now()}).eq("telegram_id",call.from_user.id).execute()
    await state.clear(); await call.answer()
    user=ensure_user(call.from_user)
    if user and user.get("is_blocked"): await call.message.edit_text("⛔ Доступ ограничен.")
    elif user: await call.message.edit_text(home_text(user),reply_markup=home_kb())
    else: await call.message.edit_text(auth_screen(code),reply_markup=auth_kb(code))


@dp.callback_query(F.data == "auth:docs")
async def auth_documentation(call:CallbackQuery):
    await call.answer()
    await call.message.edit_text(docs_screen(locale_for(call.from_user.id)),reply_markup=back_kb())


@dp.callback_query(F.data == "auth:login")
async def auth_login(call:CallbackQuery,state:FSMContext):
    await call.answer()
    user=ensure_user(call.from_user)
    if user:
        await call.message.edit_text(home_text(user),reply_markup=home_kb()); return
    code=locale_for(call.from_user.id)
    await state.clear(); await state.set_state(Auth.login)
    await call.message.answer(auth_prompt(code,"login"))


@dp.message(Auth.login)
async def auth_login_name(message:Message,state:FSMContext):
    login=(message.text or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}",login):
        await message.answer(auth_prompt(locale_for(message.from_user.id),"login")); return
    await state.update_data(auth_login=login); await state.set_state(Auth.password)
    await message.answer(auth_prompt(locale_for(message.from_user.id),"password"))


@dp.message(Auth.password)
async def auth_password(message:Message,state:FSMContext):
    code=locale_for(message.from_user.id)
    password=message.text or ""
    try: await message.delete()
    except Exception: pass
    login=(await state.get_data()).get("auth_login","")
    rows=db.table("bot_credentials").select("*").eq("login",login).limit(1).execute().data or []
    credential=rows[0] if rows else None
    valid=False
    if credential:
        locked=credential.get("locked_until")
        if locked:
            try:
                if datetime.fromisoformat(locked.replace("Z","+00:00"))>datetime.now(timezone.utc):
                    await state.clear(); await message.answer(auth_prompt(code,"failed"),reply_markup=auth_kb(code)); return
            except ValueError: pass
        try:
            salt=bytes.fromhex(credential["password_salt"])
            calculated=await asyncio.to_thread(password_hash,password,salt)
            valid=(credential.get("is_active") is True and int(credential["telegram_id"])==message.from_user.id and hmac.compare_digest(calculated,credential["password_hash"]))
        except Exception: logging.exception("credential verification failed telegram_id=%s",message.from_user.id)
    if valid:
        db.table("bot_credentials").update({"failed_attempts":0,"locked_until":None,"updated_at":now()}).eq("telegram_id",message.from_user.id).execute()
        user=register_user(message.from_user)
        await state.clear()
        if user.get("is_blocked"): await message.answer("⛔ Доступ ограничен."); return
        await message.answer(f"{auth_prompt(code,'success')}\n\n{home_text(user)}",reply_markup=home_kb()); return
    if credential and int(credential["telegram_id"]) == message.from_user.id:
        failures=int(credential.get("failed_attempts") or 0)+1
        update={"failed_attempts":failures,"updated_at":now()}
        if failures>=5: update.update({"failed_attempts":0,"locked_until":(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()})
        db.table("bot_credentials").update(update).eq("telegram_id",credential["telegram_id"]).execute()
    await state.clear()
    await message.answer(auth_prompt(code,"failed"),reply_markup=auth_kb(code))


@dp.message(Command("issue_login"))
async def issue_login(message:Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа."); return
    parts=(message.text or "").split()
    if len(parts)!=3 or not parts[1].isdigit():
        await message.answer("Использование: /issue_login <telegram_id> <login>"); return
    target_id=int(parts[1]); login=parts[2].lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}",login):
        await message.answer("Логин: 3–32 символа, латиница, цифры, точка, дефис или _."); return
    if not get_user(target_id):
        await message.answer("Пользователь должен сначала открыть бота и нажать /start."); return
    password=secrets.token_urlsafe(16); salt=secrets.token_bytes(16)
    hashed=await asyncio.to_thread(password_hash,password,salt)
    payload={"telegram_id":target_id,"login":login,"password_salt":salt.hex(),"password_hash":hashed,"is_active":True,"failed_attempts":0,"locked_until":None,"updated_at":now()}
    try: db.table("bot_credentials").upsert(payload,on_conflict="telegram_id").execute()
    except Exception:
        logging.exception("credential issue failed target_id=%s",target_id)
        await message.answer("Не удалось выдать логин. Возможно, этот логин уже занят."); return
    try:
        await bot.send_message(target_id,f"🔐 <b>Данные для входа</b>\nЛогин: <code>{login}</code>\nПароль: <code>{password}</code>\n\nНе пересылайте это сообщение. После входа удалите его из чата.")
    except Exception:
        await message.answer("Учётная запись создана, но Telegram не доставил сообщение. Пользователь должен открыть бота; затем повторите /issue_login.")
        return
    await message.answer(f"✅ Логин выдан пользователю <code>{target_id}</code>. Пароль отправлен в личный чат.")



if __name__ == "__main__":
    asyncio.run(main())
