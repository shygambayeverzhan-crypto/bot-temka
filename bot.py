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
"en":{"login":"Enter your login:","password":"Enter your password. This message will be deleted after verification.","failed":"Incorrect login or password. Please try again.","success":"✅ You are signed in.","docs":"Access credentials are issued by an administrator. Do not share your password. The password message is deleted after verification."},
"uk":{"login":"Введіть ваш логін:","password":"Введіть пароль. Повідомлення з паролем буде видалено після перевірки.","failed":"Неправильний логін або пароль. Спробуйте ще раз.","success":"✅ Вхід виконано.","docs":"Дані для входу видає адміністратор. Не передавайте пароль іншим."},
"kk":{"login":"Логиніңізді енгізіңіз:","password":"Құпиясөзді енгізіңіз. Тексерілгеннен кейін хабарлама жойылады.","failed":"Логин немесе құпиясөз қате. Қайталап көріңіз.","success":"✅ Жүйеге кірдіңіз.","docs":"Кіру деректерін әкімші береді. Құпиясөзді ешкімге жібермеңіз."},
"pl":{"login":"Wpisz swój login:","password":"Wpisz hasło. Wiadomość z hasłem zostanie usunięta po sprawdzeniu.","failed":"Nieprawidłowy login lub hasło. Spróbuj ponownie.","success":"✅ Zalogowano.","docs":"Dane dostępowe wydaje administrator. Nie udostępniaj hasła."},
"ro":{"login":"Introduceți numele de utilizator:","password":"Introduceți parola. Mesajul va fi șters după verificare.","failed":"Utilizator sau parolă incorectă. Încercați din nou.","success":"✅ Autentificare reușită.","docs":"Datele de acces sunt oferite de administrator. Nu partajați parola."},
"tr":{"login":"Kullanıcı adınızı girin:","password":"Parolanızı girin. Doğrulamadan sonra mesaj silinir.","failed":"Kullanıcı adı veya parola yanlış. Tekrar deneyin.","success":"✅ Giriş başarılı.","docs":"Giriş bilgileri yönetici tarafından verilir. Parolanızı paylaşmayın."},
"es":{"login":"Introduce tu usuario:","password":"Introduce tu contraseña. El mensaje se eliminará tras verificarla.","failed":"Usuario o contraseña incorrectos. Inténtalo de nuevo.","success":"✅ Sesión iniciada.","docs":"El administrador proporciona las credenciales. No compartas tu contraseña."},
"de":{"login":"Geben Sie Ihren Login ein:","password":"Geben Sie Ihr Passwort ein. Die Nachricht wird nach der Prüfung gelöscht.","failed":"Login oder Passwort falsch. Bitte erneut versuchen.","success":"✅ Anmeldung erfolgreich.","docs":"Zugangsdaten erhalten Sie vom Administrator. Teilen Sie Ihr Passwort nicht."},
"ky":{"login":"Логиниңизди киргизиңиз:","password":"Сырсөзүңүздү киргизиңиз. Текшерилгенден кийин билдирүү өчүрүлөт.","failed":"Логин же сырсөз туура эмес. Кайра аракет кылыңыз.","success":"✅ Кирүү ийгиликтүү болду.","docs":"Кирүү маалыматтарын администратор берет. Сырсөзүңүздү бөлүшпөңүз."},
"ka":{"login":"შეიყვანეთ თქვენი ლოგინი:","password":"შეიყვანეთ პაროლი. შემოწმების შემდეგ შეტყობინება წაიშლება.","failed":"ლოგინი ან პაროლი არასწორია. სცადეთ თავიდან.","success":"✅ შესვლა წარმატებულია.","docs":"წვდომის მონაცემებს ადმინისტრატორი გასცემს. პაროლი არ გააზიაროთ."},
"zh":{"login":"请输入您的登录名：","password":"请输入密码。验证后此消息将被删除。","failed":"登录名或密码错误，请重试。","success":"✅ 登录成功。","docs":"登录凭据由管理员提供。请勿分享密码。"},
"ko":{"login":"로그인 아이디를 입력하세요:","password":"비밀번호를 입력하세요. 확인 후 이 메시지는 삭제됩니다.","failed":"아이디 또는 비밀번호가 올바르지 않습니다. 다시 시도하세요.","success":"✅ 로그인되었습니다.","docs":"관리자가 로그인 정보를 제공합니다. 비밀번호를 공유하지 마세요."},
"ar":{"login":"أدخل اسم المستخدم:","password":"أدخل كلمة المرور. ستُحذف الرسالة بعد التحقق.","failed":"اسم المستخدم أو كلمة المرور غير صحيحة. حاول مرة أخرى.","success":"✅ تم تسجيل الدخول.","docs":"يصدر المسؤول بيانات الدخول. لا تشارك كلمة المرور."},
"ja":{"login":"ログイン名を入力してください:","password":"パスワードを入力してください。確認後、このメッセージは削除されます。","failed":"ログイン名またはパスワードが違います。もう一度お試しください。","success":"✅ ログインしました。","docs":"ログイン情報は管理者が発行します。パスワードを共有しないでください。"},
"fr":{"login":"Saisissez votre identifiant :","password":"Saisissez votre mot de passe. Le message sera supprimé après vérification.","failed":"Identifiant ou mot de passe incorrect. Réessayez.","success":"✅ Connexion réussie.","docs":"Les accès sont fournis par l’administrateur. Ne partagez pas votre mot de passe."},
"pt":{"login":"Digite seu login:","password":"Digite sua senha. A mensagem será excluída após a verificação.","failed":"Login ou senha incorretos. Tente novamente.","success":"✅ Login realizado.","docs":"O administrador fornece os dados de acesso. Não compartilhe sua senha."},
"nl":{"login":"Voer uw login in:","password":"Voer uw wachtwoord in. Dit bericht wordt na controle verwijderd.","failed":"Onjuiste login of wachtwoord. Probeer het opnieuw.","success":"✅ U bent ingelogd.","docs":"De beheerder verstrekt de inloggegevens. Deel uw wachtwoord niet."},
"hi":{"login":"अपना लॉगिन दर्ज करें:","password":"अपना पासवर्ड दर्ज करें। सत्यापन के बाद यह संदेश हटा दिया जाएगा।","failed":"लॉगिन या पासवर्ड गलत है। फिर कोशिश करें।","success":"✅ लॉगिन सफल हुआ।","docs":"लॉगिन विवरण व्यवस्थापक देता है। अपना पासवर्ड साझा न करें।"},
"sk":{"login":"Zadajte svoje prihlasovacie meno:","password":"Zadajte heslo. Správa sa po overení odstráni.","failed":"Nesprávne meno alebo heslo. Skúste znova.","success":"✅ Prihlásenie úspešné.","docs":"Prihlasovacie údaje poskytuje správca. Heslo nezdieľajte."},
}
FLOW = {
# keys: unregistered, restricted, deposit, amount_prompt, minimum, balance, withdraw, available, open_withdraw, mandatory, fee, receive, enter_tron, history, no_history, details, no_details, recipient, rules, deposit_submitted, invalid_amount, invalid_tron, order_missing, rejected, confirmed
"ru":("👋 Сначала зарегистрируйтесь.","⛔ <b>Доступ ограничен.</b>","Пополнение","Введите сумму USDT.","Минимум","Баланс","Вывод","Вывод доступен при балансе от","У вас уже есть заявка на вывод.","Обязательный вывод","Комиссия 4%","К получению","Введите адрес USDT TRC20:","История операций","Пока операций нет.","Реквизиты для пополнения","Активных реквизитов сейчас нет.","Получатель","Правила","✅ Заявка передана администратору на проверку.","❌ Введите число, например <code>250</code>.","❌ Некорректный адрес USDT TRC20.","Заявка не найдена.","❌ Заявка отклонена.","✅ Заявка подтверждена."),
"en":("👋 Please register first.","⛔ <b>Access restricted.</b>","Deposit","Enter the USDT amount.","Minimum","Balance","Withdrawal","Withdrawals are available with a balance of at least","You already have an open withdrawal request.","Required withdrawal","4% fee","You will receive","Enter your USDT TRC20 address:","Transaction history","No transactions yet.","Deposit details","No active payment details are available.","Recipient","Rules","✅ Your request has been sent to the administrator for review.","❌ Enter a number, for example <code>250</code>.","❌ Invalid USDT TRC20 address.","Request not found.","❌ The request was rejected.","✅ The request was approved."),
"uk":("👋 Спочатку зареєструйтеся.","⛔ <b>Доступ обмежено.</b>","Поповнення","Введіть суму USDT.","Мінімум","Баланс","Виведення","Виведення доступне за балансу від","У вас уже є відкрита заявка на виведення.","Обов’язкове виведення","Комісія 4%","До отримання","Введіть адресу USDT TRC20:","Історія операцій","Операцій поки немає.","Реквізити для поповнення","Активних реквізитів наразі немає.","Отримувач","Правила","✅ Заявку передано адміністратору на перевірку.","❌ Введіть число, наприклад <code>250</code>.","❌ Некоректна адреса USDT TRC20.","Заявку не знайдено.","❌ Заявку відхилено.","✅ Заявку підтверджено."),
"kk":("👋 Алдымен тіркеліңіз.","⛔ <b>Қолжетімділік шектелген.</b>","Толықтыру","USDT сомасын енгізіңіз.","Ең аз сома","Баланс","Шығару","Шығаруға қолжетімді баланс","Сізде шығару өтінімі бар.","Міндетті шығару","4% комиссия","Алатын сома","USDT TRC20 мекенжайын енгізіңіз:","Операциялар тарихы","Әзірге операциялар жоқ.","Толықтыру деректемелері","Белсенді деректемелер жоқ.","Алушы","Ережелер","✅ Өтінім тексеру үшін әкімшіге жіберілді.","❌ Сан енгізіңіз, мысалы <code>250</code>.","❌ USDT TRC20 мекенжайы қате.","Өтінім табылмады.","❌ Өтінім қабылданбады.","✅ Өтінім расталды."),
"pl":("👋 Najpierw się zarejestruj.","⛔ <b>Dostęp ograniczony.</b>","Wpłata","Wpisz kwotę USDT.","Minimum","Saldo","Wypłata","Wypłata jest dostępna przy saldzie od","Masz już otwarty wniosek o wypłatę.","Obowiązkowa wypłata","Opłata 4%","Otrzymasz","Wpisz adres USDT TRC20:","Historia operacji","Brak operacji.","Dane do wpłaty","Brak aktywnych danych do płatności.","Odbiorca","Zasady","✅ Wniosek wysłano administratorowi do sprawdzenia.","❌ Wpisz liczbę, np. <code>250</code>.","❌ Nieprawidłowy adres USDT TRC20.","Nie znaleziono wniosku.","❌ Wniosek odrzucono.","✅ Wniosek zatwierdzono."),
"ro":("👋 Înregistrați-vă mai întâi.","⛔ <b>Acces restricționat.</b>","Depunere","Introduceți suma în USDT.","Minim","Sold","Retragere","Retragerea este disponibilă de la un sold de","Aveți deja o cerere de retragere deschisă.","Retragere obligatorie","Comision 4%","Veți primi","Introduceți adresa USDT TRC20:","Istoricul operațiunilor","Nu există operațiuni încă.","Detalii pentru depunere","Nu există detalii de plată active.","Beneficiar","Reguli","✅ Cererea a fost trimisă administratorului pentru verificare.","❌ Introduceți un număr, de exemplu <code>250</code>.","❌ Adresă USDT TRC20 invalidă.","Cererea nu a fost găsită.","❌ Cererea a fost respinsă.","✅ Cererea a fost aprobată."),
"tr":("👋 Önce kayıt olun.","⛔ <b>Erişim kısıtlandı.</b>","Para yatırma","USDT miktarını girin.","Minimum","Bakiye","Para çekme","Çekim için gereken minimum bakiye","Zaten açık bir çekim talebiniz var.","Zorunlu çekim","%4 komisyon","Alacağınız tutar","USDT TRC20 adresinizi girin:","İşlem geçmişi","Henüz işlem yok.","Yatırma bilgileri","Aktif ödeme bilgisi yok.","Alıcı","Kurallar","✅ Talebiniz incelenmek üzere yöneticiye gönderildi.","❌ Bir sayı girin, örneğin <code>250</code>.","❌ Geçersiz USDT TRC20 adresi.","Talep bulunamadı.","❌ Talep reddedildi.","✅ Talep onaylandı."),
"es":("👋 Regístrate primero.","⛔ <b>Acceso restringido.</b>","Depósito","Introduce el importe en USDT.","Mínimo","Saldo","Retirada","La retirada está disponible con un saldo desde","Ya tienes una solicitud de retirada abierta.","Retirada obligatoria","Comisión del 4%","Recibirás","Introduce tu dirección USDT TRC20:","Historial de operaciones","Aún no hay operaciones.","Datos para depositar","No hay datos de pago activos.","Beneficiario","Normas","✅ La solicitud se envió al administrador para su revisión.","❌ Introduce un número, por ejemplo <code>250</code>.","❌ Dirección USDT TRC20 no válida.","Solicitud no encontrada.","❌ Solicitud rechazada.","✅ Solicitud aprobada."),
"de":("👋 Bitte registrieren Sie sich zuerst.","⛔ <b>Zugriff eingeschränkt.</b>","Einzahlung","USDT-Betrag eingeben.","Mindestbetrag","Kontostand","Auszahlung","Auszahlung ab einem Kontostand von","Sie haben bereits einen offenen Auszahlungsantrag.","Pflichtauszahlung","Gebühr 4%","Sie erhalten","USDT-TRC20-Adresse eingeben:","Transaktionsverlauf","Noch keine Transaktionen.","Einzahlungsdaten","Keine aktiven Zahlungsdaten verfügbar.","Empfänger","Regeln","✅ Ihr Antrag wurde zur Prüfung an den Administrator gesendet.","❌ Geben Sie eine Zahl ein, z. B. <code>250</code>.","❌ Ungültige USDT-TRC20-Adresse.","Antrag nicht gefunden.","❌ Antrag abgelehnt.","✅ Antrag bestätigt."),
"ky":("👋 Адегенде катталыңыз.","⛔ <b>Кирүү чектелген.</b>","Толуктоо","USDT суммасын киргизиңиз.","Минималдуу сумма","Баланс","Чыгаруу","Чыгарууга жеткиликтүү баланс","Сизде чыгаруу өтүнүчү ачык турат.","Милдеттүү чыгаруу","4% комиссия","Ала турган сумма","USDT TRC20 дарегин киргизиңиз:","Операциялар тарыхы","Азырынча операция жок.","Толуктоо реквизиттери","Жигердүү төлөм реквизиттери жок.","Алуучу","Эрежелер","✅ Өтүнүч текшерүү үчүн администраторго жөнөтүлдү.","❌ Сан киргизиңиз, мисалы <code>250</code>.","❌ USDT TRC20 дареги туура эмес.","Өтүнүч табылган жок.","❌ Өтүнүч четке кагылды.","✅ Өтүнүч ырасталды."),
"ka":("👋 ჯერ დარეგისტრირდით.","⛔ <b>წვდომა შეზღუდულია.</b>","შევსება","შეიყვანეთ USDT-ის თანხა.","მინიმუმი","ბალანსი","გატანა","გატანა ხელმისაწვდომია ბალანსისას","გატანის განაცხადი უკვე გახსნილი გაქვთ.","სავალდებულო გატანა","4% საკომისიო","მისაღები თანხა","შეიყვანეთ USDT TRC20 მისამართი:","ოპერაციების ისტორია","ოპერაციები ჯერ არ არის.","შევსების რეკვიზიტები","აქტიური გადახდის რეკვიზიტები არ არის.","მიმღები","წესები","✅ განაცხადი შესამოწმებლად ადმინისტრატორს გაეგზავნა.","❌ შეიყვანეთ რიცხვი, მაგალითად <code>250</code>.","❌ USDT TRC20 მისამართი არასწორია.","განაცხადი ვერ მოიძებნა.","❌ განაცხადი უარყოფილია.","✅ განაცხადი დადასტურდა."),
"zh":("👋 请先注册。","⛔ <b>访问受限。</b>","充值","请输入 USDT 金额。","最低金额","余额","提现","余额达到此金额后可提现","您已有一个待处理的提现申请。","必须提现","手续费 4%","到账金额","请输入 USDT TRC20 地址：","交易记录","暂无交易。","充值信息","暂无可用的付款信息。","收款人","规则","✅ 申请已发送给管理员审核。","❌ 请输入数字，例如 <code>250</code>。","❌ USDT TRC20 地址无效。","未找到申请。","❌ 申请已拒绝。","✅ 申请已确认。"),
"ko":("👋 먼저 가입해 주세요.","⛔ <b>접근이 제한되었습니다.</b>","입금","USDT 금액을 입력하세요.","최소 금액","잔액","출금","출금 가능 최소 잔액","이미 진행 중인 출금 신청이 있습니다.","필수 출금","수수료 4%","받을 금액","USDT TRC20 주소를 입력하세요:","거래 내역","아직 거래 내역이 없습니다.","입금 정보","사용 가능한 결제 정보가 없습니다.","받는 사람","규칙","✅ 신청이 관리자에게 검토 요청되었습니다.","❌ 숫자를 입력하세요. 예: <code>250</code>.","❌ 잘못된 USDT TRC20 주소입니다.","신청을 찾을 수 없습니다.","❌ 신청이 거절되었습니다.","✅ 신청이 승인되었습니다."),
"ar":("👋 يرجى التسجيل أولاً.","⛔ <b>الوصول مقيّد.</b>","إيداع","أدخل مبلغ USDT.","الحد الأدنى","الرصيد","سحب","يتاح السحب عند رصيد لا يقل عن","لديك طلب سحب مفتوح بالفعل.","سحب إلزامي","رسوم 4%","المبلغ المستلم","أدخل عنوان USDT TRC20:","سجل العمليات","لا توجد عمليات بعد.","تفاصيل الإيداع","لا توجد بيانات دفع نشطة.","المستلم","القواعد","✅ أُرسل الطلب إلى المسؤول للمراجعة.","❌ أدخل رقماً، مثل <code>250</code>.","❌ عنوان USDT TRC20 غير صالح.","لم يتم العثور على الطلب.","❌ تم رفض الطلب.","✅ تمت الموافقة على الطلب."),
"ja":("👋 先に登録してください。","⛔ <b>アクセスが制限されています。</b>","入金","USDTの金額を入力してください。","最低額","残高","出金","残高が次の金額以上で出金できます","出金申請はすでにあります。","必須出金","手数料 4%","受取額","USDT TRC20アドレスを入力してください:","取引履歴","取引はまだありません。","入金情報","有効な支払い情報はありません。","受取人","ルール","✅ 申請を管理者に送り、確認を依頼しました。","❌ 数字を入力してください（例：<code>250</code>）。","❌ USDT TRC20アドレスが無効です。","申請が見つかりません。","❌ 申請は却下されました。","✅ 申請が承認されました。"),
"fr":("👋 Inscrivez-vous d’abord.","⛔ <b>Accès restreint.</b>","Dépôt","Saisissez le montant en USDT.","Minimum","Solde","Retrait","Le retrait est disponible à partir d’un solde de","Vous avez déjà une demande de retrait ouverte.","Retrait obligatoire","Frais de 4%","Montant reçu","Saisissez votre adresse USDT TRC20 :","Historique des opérations","Aucune opération pour le moment.","Coordonnées de dépôt","Aucune coordonnée de paiement active.","Bénéficiaire","Règles","✅ Votre demande a été envoyée à l’administrateur pour vérification.","❌ Saisissez un nombre, par exemple <code>250</code>.","❌ Adresse USDT TRC20 invalide.","Demande introuvable.","❌ Demande rejetée.","✅ Demande confirmée."),
"pt":("👋 Registre-se primeiro.","⛔ <b>Acesso restrito.</b>","Depósito","Digite o valor em USDT.","Mínimo","Saldo","Saque","O saque está disponível com saldo a partir de","Você já tem uma solicitação de saque aberta.","Saque obrigatório","Taxa de 4%","Valor a receber","Digite seu endereço USDT TRC20:","Histórico de operações","Ainda não há operações.","Dados para depósito","Não há dados de pagamento ativos.","Beneficiário","Regras","✅ Sua solicitação foi enviada ao administrador para análise.","❌ Digite um número, por exemplo <code>250</code>.","❌ Endereço USDT TRC20 inválido.","Solicitação não encontrada.","❌ Solicitação rejeitada.","✅ Solicitação confirmada."),
"nl":("👋 Registreer u eerst.","⛔ <b>Toegang beperkt.</b>","Storting","Voer het USDT-bedrag in.","Minimum","Saldo","Opname","Opnemen kan vanaf een saldo van","U heeft al een open opnameverzoek.","Verplichte opname","Kosten 4%","U ontvangt","Voer uw USDT TRC20-adres in:","Transactiegeschiedenis","Nog geen transacties.","Stortingsgegevens","Er zijn geen actieve betaalgegevens.","Ontvanger","Regels","✅ Uw verzoek is ter controle naar de beheerder gestuurd.","❌ Voer een getal in, bijvoorbeeld <code>250</code>.","❌ Ongeldig USDT TRC20-adres.","Verzoek niet gevonden.","❌ Verzoek afgewezen.","✅ Verzoek bevestigd."),
"hi":("👋 कृपया पहले पंजीकरण करें।","⛔ <b>पहुंच प्रतिबंधित है।</b>","जमा","USDT राशि दर्ज करें।","न्यूनतम","शेष राशि","निकासी","इस शेष राशि से निकासी उपलब्ध है","आपका निकासी अनुरोध पहले से खुला है।","अनिवार्य निकासी","4% शुल्क","आपको मिलेगा","अपना USDT TRC20 पता दर्ज करें:","लेन-देन इतिहास","अभी कोई लेन-देन नहीं है।","जमा विवरण","कोई सक्रिय भुगतान विवरण उपलब्ध नहीं है।","प्राप्तकर्ता","नियम","✅ आपका अनुरोध समीक्षा के लिए व्यवस्थापक को भेजा गया।","❌ संख्या दर्ज करें, उदाहरण <code>250</code>।","❌ अमान्य USDT TRC20 पता।","अनुरोध नहीं मिला।","❌ अनुरोध अस्वीकार किया गया।","✅ अनुरोध स्वीकृत हुआ।"),
"sk":("👋 Najprv sa zaregistrujte.","⛔ <b>Prístup je obmedzený.</b>","Vklad","Zadajte sumu v USDT.","Minimum","Zostatok","Výber","Výber je dostupný pri zostatku od","Už máte otvorenú žiadosť o výber.","Povinný výber","Poplatok 4%","Dostanete","Zadajte adresu USDT TRC20:","História operácií","Zatiaľ žiadne operácie.","Údaje na vklad","Nie sú dostupné aktívne platobné údaje.","Príjemca","Pravidlá","✅ Žiadosť bola odoslaná správcovi na kontrolu.","❌ Zadajte číslo, napríklad <code>250</code>.","❌ Neplatná adresa USDT TRC20.","Žiadosť sa nenašla.","❌ Žiadosť bola zamietnutá.","✅ Žiadosť bola potvrdená."),
}
FLOW_KEYS=("unregistered","restricted","deposit","amount_prompt","minimum","balance","withdraw","available","open_withdraw","mandatory","fee","receive","enter_tron","history","no_history","details","no_details","recipient","rules","deposit_submitted","invalid_amount","invalid_tron","order_missing","rejected","confirmed")
FLOW_EXTRA = {
# unavailable, open_deposit, amount_label, network_label, address_label, txid_instruction, txid_invalid, receipt_instruction, receipt_pending, delivery_failed, expired, paid, rejected_user, withdraw_created, withdraw_paid, withdraw_rejected
"ru":("Сначала необходимо вывести весь баланс.","У вас уже есть открытая заявка на депозит.","Сумма","Сеть","Адрес для оплаты","После перевода отправьте TXID или чек.","Отправьте корректный TXID TRC20 или приложите фото/файл чека.","Отправьте TXID TRC20 текстом или приложите фото/файл чека.","✅ Заявка передана администратору на проверку.","Заявка сохранена, но уведомление администратору не доставлено. Попробуйте отправить TXID или чек ещё раз.","⌛ Заявка истекла.","✅ Оплата подтверждена.","❌ Заявка отклонена администратором.","Заявка создана","✅ Вывод подтверждён","❌ Withdrawal rejected. Your balance is unchanged."),
"en":("Please withdraw your full balance first.","You already have an open deposit request.","Amount","Network","Payment address","After payment, send the TXID or receipt.","Send a valid TRC20 TXID or attach a receipt image/file.","Send the TRC20 TXID as text or attach a receipt image/file.","✅ Your request has been sent to the administrator for review.","The request was saved, but the administrator was not notified. Please send the TXID or receipt again.","⌛ The request expired.","✅ Payment confirmed.","❌ The request was rejected by the administrator.","Request created","✅ Withdrawal confirmed","❌ Withdrawal rejected. Your balance is unchanged."),
"uk":("Спочатку виведіть увесь баланс.","У вас уже є відкрита заявка на поповнення.","Сума","Мережа","Адреса для оплати","Після оплати надішліть TXID або чек.","Надішліть коректний TXID TRC20 або додайте фото/файл чека.","Надішліть TXID TRC20 текстом або додайте фото/файл чека.","✅ Заявку передано адміністратору на перевірку.","Заявку збережено, але адміністратора не сповіщено. Надішліть TXID або чек ще раз.","⌛ Термін заявки минув.","✅ Оплату підтверджено.","❌ Заявку відхилено адміністратором.","Заявку створено","✅ Виведення підтверджено","❌ Виведення відхилено. Баланс не змінено."),
"kk":("Алдымен толық балансты шығарыңыз.","Сізде депозит өтінімі ашық тұр.","Сома","Желі","Төлем мекенжайы","Төлемнен кейін TXID немесе түбіртекті жіберіңіз.","Дұрыс TRC20 TXID жіберіңіз немесе түбіртек суретін/файлын тіркеңіз.","TRC20 TXID мәтінін немесе түбіртек суретін/файлын жіберіңіз.","✅ Өтінім әкімшіге тексеруге жіберілді.","Өтінім сақталды, бірақ әкімшіге хабарланбады. TXID не түбіртекті қайта жіберіңіз.","⌛ Өтінімнің мерзімі аяқталды.","✅ Төлем расталды.","❌ Өтінімді әкімші қабылдамады.","Өтінім жасалды","✅ Шығару расталды","❌ Шығару қабылданбады. Баланс өзгерген жоқ."),
"pl":("Najpierw wypłać całe saldo.","Masz już otwarty wniosek o wpłatę.","Kwota","Sieć","Adres płatności","Po wpłacie wyślij TXID lub potwierdzenie.","Wyślij prawidłowy TXID TRC20 lub załącz zdjęcie/plik potwierdzenia.","Wyślij TXID TRC20 jako tekst lub załącz zdjęcie/plik potwierdzenia.","✅ Wniosek wysłano administratorowi do sprawdzenia.","Wniosek zapisano, ale administrator nie otrzymał powiadomienia. Wyślij TXID lub potwierdzenie ponownie.","⌛ Wniosek wygasł.","✅ Płatność potwierdzona.","❌ Administrator odrzucił wniosek.","Wniosek utworzono","✅ Wypłatę potwierdzono","❌ Wypłata odrzucona. Saldo bez zmian."),
"ro":("Retrageți mai întâi întregul sold.","Aveți deja o cerere de depunere deschisă.","Sumă","Rețea","Adresă de plată","După plată, trimiteți TXID-ul sau chitanța.","Trimiteți un TXID TRC20 valid sau atașați o imagine/un fișier cu chitanța.","Trimiteți TXID-ul TRC20 ca text sau atașați chitanța.","✅ Cererea a fost trimisă administratorului pentru verificare.","Cererea a fost salvată, dar administratorul nu a fost notificat. Retrimiteți TXID-ul sau chitanța.","⌛ Cererea a expirat.","✅ Plata a fost confirmată.","❌ Cererea a fost respinsă de administrator.","Cerere creată","✅ Retragere confirmată","❌ Retragere respinsă. Soldul nu s-a schimbat."),
"tr":("Önce bakiyenizin tamamını çekin.","Zaten açık bir yatırma talebiniz var.","Tutar","Ağ","Ödeme adresi","Ödemeden sonra TXID veya makbuz gönderin.","Geçerli bir TRC20 TXID gönderin veya makbuz görseli/dosyası ekleyin.","TRC20 TXID'yi metin olarak gönderin veya makbuz ekleyin.","✅ Talebiniz yöneticiye inceleme için gönderildi.","Talep kaydedildi ancak yöneticiye bildirim ulaşmadı. TXID veya makbuzu tekrar gönderin.","⌛ Talebin süresi doldu.","✅ Ödeme onaylandı.","❌ Talep yönetici tarafından reddedildi.","Talep oluşturuldu","✅ Para çekme onaylandı","❌ Para çekme reddedildi. Bakiyeniz değişmedi."),
"es":("Retira primero todo tu saldo.","Ya tienes una solicitud de depósito abierta.","Importe","Red","Dirección de pago","Después del pago, envía el TXID o el recibo.","Envía un TXID TRC20 válido o adjunta una imagen/archivo del recibo.","Envía el TXID TRC20 como texto o adjunta el recibo.","✅ La solicitud se envió al administrador para su revisión.","La solicitud se guardó, pero no se avisó al administrador. Vuelve a enviar el TXID o el recibo.","⌛ La solicitud ha caducado.","✅ Pago confirmado.","❌ El administrador rechazó la solicitud.","Solicitud creada","✅ Retirada confirmada","❌ Retirada rechazada. El saldo no cambió."),
"de":("Bitte zahlen Sie zuerst Ihr gesamtes Guthaben aus.","Sie haben bereits einen offenen Einzahlungsantrag.","Betrag","Netzwerk","Zahlungsadresse","Senden Sie nach der Zahlung die TXID oder den Beleg.","Senden Sie eine gültige TRC20-TXID oder fügen Sie den Beleg als Bild/Datei an.","Senden Sie die TRC20-TXID als Text oder fügen Sie den Beleg an.","✅ Ihr Antrag wurde zur Prüfung an den Administrator gesendet.","Der Antrag wurde gespeichert, aber der Administrator nicht benachrichtigt. Senden Sie TXID oder Beleg erneut.","⌛ Der Antrag ist abgelaufen.","✅ Zahlung bestätigt.","❌ Der Administrator hat den Antrag abgelehnt.","Antrag erstellt","✅ Auszahlung bestätigt","❌ Auszahlung abgelehnt. Ihr Guthaben bleibt unverändert."),
"ky":("Алгач толук балансты чыгарыңыз.","Сизде толуктоо өтүнүчү ачык турат.","Сумма","Тармак","Төлөм дареги","Төлөгөндөн кийин TXID же чекти жөнөтүңүз.","Туура TRC20 TXID жөнөтүңүз же чек сүрөтүн/файлын тиркеңиз.","TRC20 TXID текстин же чек сүрөтүн/файлын жөнөтүңүз.","✅ Өтүнүч текшерүү үчүн администраторго жөнөтүлдү.","Өтүнүч сакталды, бирок администраторго кабар берилген жок. TXID же чекти кайра жөнөтүңүз.","⌛ Өтүнүчтүн мөөнөтү бүттү.","✅ Төлөм ырасталды.","❌ Өтүнүч администратор тарабынан четке кагылды.","Өтүнүч түзүлдү","✅ Чыгаруу ырасталды","❌ Чыгаруу четке кагылды. Баланс өзгөргөн жок."),
"ka":("ჯერ მთელი ბალანსი გაიტანეთ.","შევსების განაცხადი უკვე გახსნილი გაქვთ.","თანხა","ქსელი","გადახდის მისამართი","გადახდის შემდეგ გამოგზავნეთ TXID ან ქვითარი.","გამოგზავნეთ სწორი TRC20 TXID ან დაურთეთ ქვითრის ფოტო/ფაილი.","გამოგზავნეთ TRC20 TXID ტექსტად ან დაურთეთ ქვითარი.","✅ განაცხადი შესამოწმებლად ადმინისტრატორს გაეგზავნა.","განაცხადი შენახულია, თუმცა ადმინისტრატორს შეტყობინება არ მიუღია. ხელახლა გამოგზავნეთ TXID ან ქვითარი.","⌛ განაცხადის ვადა ამოიწურა.","✅ გადახდა დადასტურდა.","❌ ადმინისტრატორმა განაცხადი უარყო.","განაცხადი შეიქმნა","✅ გატანა დადასტურდა","❌ გატანა უარყოფილია. ბალანსი უცვლელია."),
"zh":("请先提取全部余额。","您已有一个待处理的充值申请。","金额","网络","付款地址","付款后请发送 TXID 或收据。","请发送有效的 TRC20 TXID，或附上收据图片/文件。","请以文字发送 TRC20 TXID，或附上收据图片/文件。","✅ 申请已发送给管理员审核。","申请已保存，但未通知管理员。请重新发送 TXID 或收据。","⌛ 申请已过期。","✅ 付款已确认。","❌ 管理员已拒绝该申请。","申请已创建","✅ 提现已确认。","❌ 提现已拒绝，余额未更改。"),
"ko":("먼저 잔액을 모두 출금하세요.","이미 진행 중인 입금 신청이 있습니다.","금액","네트워크","결제 주소","결제 후 TXID 또는 영수증을 보내세요.","유효한 TRC20 TXID를 보내거나 영수증 이미지/파일을 첨부하세요.","TRC20 TXID를 문자로 보내거나 영수증을 첨부하세요.","✅ 신청이 관리자에게 검토 요청되었습니다.","신청은 저장되었지만 관리자에게 알림이 전달되지 않았습니다. TXID 또는 영수증을 다시 보내세요.","⌛ 신청이 만료되었습니다.","✅ 결제가 확인되었습니다.","❌ 관리자가 신청을 거절했습니다.","신청이 생성되었습니다","✅ 출금이 확인되었습니다.","❌ 출금이 거절되었습니다. 잔액은 변경되지 않았습니다."),
"ar":("يرجى سحب رصيدك بالكامل أولاً.","لديك طلب إيداع مفتوح بالفعل.","المبلغ","الشبكة","عنوان الدفع","بعد الدفع، أرسل TXID أو الإيصال.","أرسل TXID صالحاً لشبكة TRC20 أو أرفق صورة/ملف الإيصال.","أرسل TXID نصاً أو أرفق صورة/ملف الإيصال.","✅ أُرسل الطلب إلى المسؤول للمراجعة.","تم حفظ الطلب لكن لم يصل إشعار للمسؤول. أعد إرسال TXID أو الإيصال.","⌛ انتهت صلاحية الطلب.","✅ تم تأكيد الدفع.","❌ رفض المسؤول الطلب.","تم إنشاء الطلب","✅ تم تأكيد السحب","❌ رُفض السحب. لم يتغير رصيدك."),
"ja":("先に残高をすべて出金してください。","入金申請はすでにあります。","金額","ネットワーク","支払先アドレス","支払い後、TXIDまたは領収書を送信してください。","有効なTRC20 TXIDを送信するか、領収書の画像/ファイルを添付してください。","TRC20 TXIDをテキストで送るか、領収書を添付してください。","✅ 申請を管理者に送り、確認を依頼しました。","申請は保存されましたが管理者に通知されませんでした。TXIDまたは領収書を再送してください。","⌛ 申請の期限が切れました。","✅ 支払いが確認されました。","❌ 管理者が申請を却下しました。","申請を作成しました","✅ 出金が確認されました。","❌ 出金は却下されました。残高に変更はありません。"),
"fr":("Veuillez d’abord retirer tout votre solde.","Vous avez déjà une demande de dépôt ouverte.","Montant","Réseau","Adresse de paiement","Après le paiement, envoyez le TXID ou le reçu.","Envoyez un TXID TRC20 valide ou joignez une image/un fichier du reçu.","Envoyez le TXID TRC20 en texte ou joignez le reçu.","✅ Votre demande a été envoyée à l’administrateur pour vérification.","La demande a été enregistrée, mais l’administrateur n’a pas été averti. Renvoyez le TXID ou le reçu.","⌛ La demande a expiré.","✅ Paiement confirmé.","❌ La demande a été rejetée par l’administrateur.","Demande créée","✅ Retrait confirmé","❌ Retrait refusé. Votre solde est inchangé."),
"pt":("Retire primeiro todo o seu saldo.","Você já tem uma solicitação de depósito aberta.","Valor","Rede","Endereço de pagamento","Após o pagamento, envie o TXID ou o comprovante.","Envie um TXID TRC20 válido ou anexe uma imagem/arquivo do comprovante.","Envie o TXID TRC20 em texto ou anexe o comprovante.","✅ Sua solicitação foi enviada ao administrador para análise.","A solicitação foi salva, mas o administrador não foi notificado. Envie o TXID ou comprovante novamente.","⌛ A solicitação expirou.","✅ Pagamento confirmado.","❌ O administrador rejeitou a solicitação.","Solicitação criada","✅ Saque confirmado","❌ Saque rejeitado. Seu saldo não foi alterado."),
"nl":("Neem eerst uw volledige saldo op.","U heeft al een open stortingsverzoek.","Bedrag","Netwerk","Betaaladres","Stuur na betaling de TXID of het betalingsbewijs.","Stuur een geldige TRC20-TXID of voeg een afbeelding/bestand van het bewijs toe.","Stuur de TRC20-TXID als tekst of voeg het bewijs toe.","✅ Uw verzoek is ter controle naar de beheerder gestuurd.","Het verzoek is opgeslagen, maar de beheerder is niet geïnformeerd. Stuur de TXID of het bewijs opnieuw.","⌛ Het verzoek is verlopen.","✅ Betaling bevestigd.","❌ De beheerder heeft het verzoek afgewezen.","Verzoek aangemaakt","✅ Opname bevestigd","❌ Opname afgewezen. Uw saldo is ongewijzigd."),
"hi":("पहले अपनी पूरी शेष राशि निकालें।","आपका जमा अनुरोध पहले से खुला है।","राशि","नेटवर्क","भुगतान पता","भुगतान के बाद TXID या रसीद भेजें।","मान्य TRC20 TXID भेजें या रसीद की छवि/फ़ाइल संलग्न करें।","TRC20 TXID टेक्स्ट में भेजें या रसीद संलग्न करें।","✅ आपका अनुरोध समीक्षा के लिए व्यवस्थापक को भेजा गया।","अनुरोध सहेजा गया, लेकिन व्यवस्थापक को सूचना नहीं मिली। TXID या रसीद फिर भेजें।","⌛ अनुरोध की समय-सीमा समाप्त हुई।","✅ भुगतान की पुष्टि हुई।","❌ व्यवस्थापक ने अनुरोध अस्वीकार किया।","अनुरोध बनाया गया","✅ निकासी की पुष्टि हुई।","❌ निकासी अस्वीकार हुई। शेष राशि नहीं बदली।"),
"sk":("Najprv vyberte celý zostatok.","Už máte otvorenú žiadosť o vklad.","Suma","Sieť","Adresa platby","Po platbe pošlite TXID alebo potvrdenie.","Pošlite platný TRC20 TXID alebo priložte obrázok/súbor potvrdenia.","Pošlite TXID TRC20 ako text alebo priložte potvrdenie.","✅ Žiadosť bola odoslaná správcovi na kontrolu.","Žiadosť bola uložená, ale správca nebol upozornený. Znova pošlite TXID alebo potvrdenie.","⌛ Platnosť žiadosti vypršala.","✅ Platba potvrdená.","❌ Správca žiadosť zamietol.","Žiadosť vytvorená","✅ Výber potvrdený","❌ Výber zamietnutý. Zostatok sa nezmenil."),
}
FLOW_EXTRA_KEYS=("unavailable","open_deposit","amount_label","network_label","address_label","txid_instruction","txid_invalid","receipt_instruction","receipt_pending","delivery_failed","expired","paid","rejected_user","withdraw_created","withdraw_paid","withdraw_rejected")
def flow_extra(code,key):
    return FLOW_EXTRA.get(code,FLOW_EXTRA["en"])[FLOW_EXTRA_KEYS.index(key)]
def amount_prompt(code):
    return f"💰 <b>{flow(code,'deposit')}</b>\n\n{flow(code,'amount_prompt')}\n{flow(code,'minimum')}: <b>{money(MIN_DEPOSIT)} USDT</b>\n{flow_extra(code,'network_label')}: <b>TRC20</b>"

def locale_for(telegram_id):
    try:
        rows = db.table("bot_users").select("language").eq("telegram_id", telegram_id).limit(1).execute().data or []
        code = rows[0].get("language") if rows else "ru"
        return code if code in AUTH_COPY else "ru"
    except Exception:
        logging.exception("language lookup failed telegram_id=%s", telegram_id)
        return "ru"
def locale_for(telegram_id):
    rows = db.table("bot_users").select("language").eq("telegram_id", telegram_id).limit(1).execute().data or []
    code = rows[0].get("language") if rows else "ru"
    return code if code in AUTH_COPY else "ru"


def ui(code,key):
    values=AUTH_COPY.get(code,AUTH_COPY["ru"])
    return values[AUTH_KEYS.index(key)]
def auth_prompt(code,key):
    return AUTH_PROMPTS.get(code,AUTH_PROMPTS["ru"])[key]
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


HOME_COPY = {
"ru":("Ваш личный финансовый кабинет","Привет","Баланс","Аккаунт активен","Выберите действие ниже.","💰 Внести депозит","💳 Баланс","📤 Вывести средства","📜 История","ℹ️ Помощь","🌐 Язык","⬅️ Главное меню","📋 Скопировать TRC20-адрес"),
"en":("Your personal finance account","Hello","Balance","Account active","Choose an action below.","💰 Make a deposit","💳 Balance","📤 Withdraw funds","📜 History","ℹ️ Help","🌐 Language","⬅️ Main menu","📋 Copy TRC20 address"),
"uk":("Ваш особистий фінансовий кабінет","Вітаємо","Баланс","Обліковий запис активний","Виберіть дію нижче.","💰 Поповнити депозит","💳 Баланс","📤 Вивести кошти","📜 Історія","ℹ️ Допомога","🌐 Мова","⬅️ Головне меню","📋 Скопіювати TRC20-адресу"),
"kk":("Жеке қаржы кабинеті","Сәлем","Баланс","Аккаунт белсенді","Төмендегі әрекетті таңдаңыз.","💰 Депозит енгізу","💳 Баланс","📤 Қаражатты шығару","📜 Тарих","ℹ️ Көмек","🌐 Тіл","⬅️ Басты мәзір","📋 TRC20 мекенжайын көшіру"),
"pl":("Twoje konto finansowe","Witaj","Saldo","Konto aktywne","Wybierz działanie poniżej.","💰 Wpłać depozyt","💳 Saldo","📤 Wypłać środki","📜 Historia","ℹ️ Pomoc","🌐 Język","⬅️ Menu główne","📋 Kopiuj adres TRC20"),
"ro":("Contul dvs. financiar","Bună","Sold","Cont activ","Alegeți o acțiune.","💰 Depuneți","💳 Sold","📤 Retrageți fonduri","📜 Istoric","ℹ️ Ajutor","🌐 Limbă","⬅️ Meniul principal","📋 Copiază adresa TRC20"),
"tr":("Kişisel finans hesabınız","Merhaba","Bakiye","Hesap aktif","Aşağıdan bir işlem seçin.","💰 Yatırma yap","💳 Bakiye","📤 Para çek","📜 Geçmiş","ℹ️ Yardım","🌐 Dil","⬅️ Ana menü","📋 TRC20 adresini kopyala"),
"es":("Tu cuenta financiera personal","Hola","Saldo","Cuenta activa","Elige una opción.","💰 Depositar","💳 Saldo","📤 Retirar fondos","📜 Historial","ℹ️ Ayuda","🌐 Idioma","⬅️ Menú principal","📋 Copiar dirección TRC20"),
"de":("Ihr persönliches Finanzkonto","Hallo","Kontostand","Konto aktiv","Wählen Sie eine Aktion.","💰 Einzahlung","💳 Kontostand","📤 Guthaben auszahlen","📜 Verlauf","ℹ️ Hilfe","🌐 Sprache","⬅️ Hauptmenü","📋 TRC20-Adresse kopieren"),
"ky":("Жеке каржы эсебиңиз","Салам","Баланс","Аккаунт активдүү","Төмөндөн аракетти тандаңыз.","💰 Депозит салуу","💳 Баланс","📤 Каражат чыгаруу","📜 Тарых","ℹ️ Жардам","🌐 Тил","⬅️ Башкы меню","📋 TRC20 дарегин көчүрүү"),
"ka":("თქვენი პირადი ფინანსური ანგარიში","გამარჯობა","ბალანსი","ანგარიში აქტიურია","აირჩიეთ მოქმედება ქვემოთ.","💰 დეპოზიტის შეტანა","💳 ბალანსი","📤 თანხის გატანა","📜 ისტორია","ℹ️ დახმარება","🌐 ენა","⬅️ მთავარი მენიუ","📋 TRC20 მისამართის კოპირება"),
"zh":("您的个人财务账户","您好","余额","账户正常","请选择操作。","💰 充值","💳 余额","📤 提现","📜 记录","ℹ️ 帮助","🌐 语言","⬅️ 主菜单","📋 复制 TRC20 地址"),
"ko":("개인 금융 계정","안녕하세요","잔액","계정 활성","아래에서 작업을 선택하세요.","💰 입금","💳 잔액","📤 출금","📜 내역","ℹ️ 도움말","🌐 언어","⬅️ 메인 메뉴","📋 TRC20 주소 복사"),
"ar":("حسابك المالي الشخصي","مرحبًا","الرصيد","الحساب نشط","اختر إجراءً أدناه.","💰 إيداع","💳 الرصيد","📤 سحب الأموال","📜 السجل","ℹ️ المساعدة","🌐 اللغة","⬅️ القائمة الرئيسية","📋 نسخ عنوان TRC20"),
"ja":("あなたの個人金融アカウント","こんにちは","残高","アカウント有効","下から操作を選択してください。","💰 入金","💳 残高","📤 出金","📜 履歴","ℹ️ ヘルプ","🌐 言語","⬅️ メインメニュー","📋 TRC20アドレスをコピー"),
"fr":("Votre compte financier personnel","Bonjour","Solde","Compte actif","Choisissez une action ci-dessous.","💰 Déposer","💳 Solde","📤 Retirer des fonds","📜 Historique","ℹ️ Aide","🌐 Langue","⬅️ Menu principal","📋 Copier l’adresse TRC20"),
"pt":("Sua conta financeira pessoal","Olá","Saldo","Conta ativa","Escolha uma ação abaixo.","💰 Depositar","💳 Saldo","📤 Retirar fundos","📜 Histórico","ℹ️ Ajuda","🌐 Idioma","⬅️ Menu principal","📋 Copiar endereço TRC20"),
"nl":("Uw persoonlijke financiële account","Hallo","Saldo","Account actief","Kies hieronder een actie.","💰 Storten","💳 Saldo","📤 Geld opnemen","📜 Geschiedenis","ℹ️ Help","🌐 Taal","⬅️ Hoofdmenu","📋 TRC20-adres kopiëren"),
"hi":("आपका व्यक्तिगत वित्त खाता","नमस्ते","शेष राशि","खाता सक्रिय है","नीचे कोई विकल्प चुनें।","💰 जमा करें","💳 शेष राशि","📤 धन निकालें","📜 इतिहास","ℹ️ सहायता","🌐 भाषा","⬅️ मुख्य मेनू","📋 TRC20 पता कॉपी करें"),
"sk":("Váš osobný finančný účet","Dobrý deň","Zostatok","Účet je aktívny","Vyberte si akciu.","💰 Vložiť vklad","💳 Zostatok","📤 Vybrať prostriedky","📜 História","ℹ️ Pomoc","🌐 Jazyk","⬅️ Hlavné menu","📋 Kopírovať TRC20 adresu"),
}
def register_kb():
    return auth_kb("ru")

def home_kb(code="ru"):
    labels=HOME_COPY.get(code,HOME_COPY["ru"])
    b = InlineKeyboardBuilder()
    b.button(text=labels[5], callback_data="menu:deposit")
    b.button(text=labels[6], callback_data="menu:balance")
    b.button(text=labels[7], callback_data="menu:withdraw")
    b.button(text=labels[8], callback_data="menu:history")
    b.button(text=labels[9], callback_data="menu:help")
    b.button(text=labels[10], callback_data="menu:language")
    b.adjust(1,2,2,1,1); return b.as_markup()

def back_kb(code="ru"):
    b=InlineKeyboardBuilder(); b.button(text=HOME_COPY.get(code,HOME_COPY["ru"])[11],callback_data="menu:home"); return b.as_markup()

def address_copy_kb(address,code="ru"):
    labels=HOME_COPY.get(code,HOME_COPY["ru"])
    b = InlineKeyboardBuilder()
    b.button(
        text=labels[12],
        copy_text=CopyTextButton(text=address),
    )
    b.button(text=labels[11], callback_data="menu:home")
    b.adjust(1, 1)
    return b.as_markup()


def balance_kb(code="ru"):
    labels=HOME_COPY.get(code,HOME_COPY["ru"]); b=InlineKeyboardBuilder(); b.button(text=labels[5],callback_data="menu:deposit"); b.button(text=labels[7],callback_data="menu:withdraw"); b.button(text=labels[11],callback_data="menu:home"); b.adjust(1,2); return b.as_markup()

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
    code=u.get("language") or "ru"
    c=HOME_COPY.get(code,HOME_COPY["ru"])
    name=u.get("first_name") or c[1]
    balance_value=money(u["balance"])
    return (f"👋 <b>4% TRADER</b>\n<i>{c[0]}</i>\n\n{c[1]}, <b>{name}</b>!\n\n💰 <b>{c[2]}</b>\n<code>{balance_value} USDT</code>\n\n🟢 <b>{c[3]}</b>\n{c[4]}")

async def show_home(target,tg):
    row=ensure_identity(tg)
    code=(row or {}).get("language") or "ru"
    if not (row or {}).get("language_selected"):
        text=ui(code,"choose"); markup=language_picker_kb()
    else:
        u=ensure_user(tg)
        if not u: text=auth_screen(code); markup=auth_kb(code)
        elif u.get("is_blocked"): text="⛔ Доступ ограничен."; markup=None
        else: text=home_text(u); markup=home_kb(code)
    if isinstance(target,CallbackQuery):
        await target.message.edit_text(text,reply_markup=markup); await target.answer()
    else:
        await target.answer(text,reply_markup=markup)

async def start_deposit_flow(message,state,tg):
    code=locale_for(tg.id)
    u=ensure_user(tg)
    if not u: await message.answer(flow(code,"unregistered"),reply_markup=auth_kb(code)); return
    if u["is_blocked"]: await message.answer(flow(code,"restricted"),reply_markup=back_kb(code)); return
    if Decimal(str(u["balance"]))>=THRESHOLD:
        await message.answer(f"⚠️ <b>{flow(code,'deposit')} unavailable.</b>\n{flow_extra(code,'unavailable')}",reply_markup=back_kb(code)); return
    if open_order(u["id"]):
        await message.answer(f"⚠️ {flow_extra(code,'open_deposit')}",reply_markup=back_kb(code)); return
    await state.set_state(Deposit.amount)
    await message.answer(amount_prompt(code),reply_markup=back_kb(code))

@dp.callback_query(F.data == "menu:home")
async def menu_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(call, call.from_user)

@dp.callback_query(F.data == "menu:balance")
async def menu_balance(call: CallbackQuery):
    code=locale_for(call.from_user.id)
    u=ensure_user(call.from_user)
    if not u: await call.answer(flow(code,"unregistered"),show_alert=True); return
    await call.message.edit_text(f"💳 <b>{flow(code,'balance')}</b>\n\n<b>{money(u['balance'])} USDT</b>\n{flow_extra(code,'network_label')}: <b>TRC20</b>",reply_markup=balance_kb(code))
    await call.answer()

@dp.callback_query(F.data == "menu:withdraw")
async def menu_withdraw(call: CallbackQuery,state:FSMContext):
    code=locale_for(call.from_user.id)
    u=ensure_user(call.from_user)
    if not u: await call.answer(flow(code,"unregistered"),show_alert=True); return
    bal=Decimal(str(u["balance"]))
    if bal<THRESHOLD:
        await call.message.edit_text(f"📤 <b>{flow(code,'withdraw')}</b>\n\n{flow(code,'available')} <b>500 USDT</b>.\n{flow(code,'balance')}: <b>{money(bal)} USDT</b>.",reply_markup=back_kb(code))
        await call.answer(); return
    if open_withdrawal(u["id"]):
        await call.message.edit_text(f"⏳ {flow(code,'open_withdraw')}",reply_markup=back_kb(code)); await call.answer(); return
    fee=(bal*FEE_RATE).quantize(Decimal("0.01"),rounding=ROUND_DOWN); net=bal-fee
    await state.set_state(Withdrawal.address)
    await call.message.edit_text(f"📤 <b>{flow(code,'mandatory')}</b>\n\n{flow(code,'balance')}: <b>{money(bal)} USDT</b>\n{flow(code,'fee')}: <b>{money(fee)} USDT</b>\n{flow(code,'receive')}: <b>{money(net)} USDT</b>\n\n{flow(code,'enter_tron')}",reply_markup=back_kb(code))
    await call.answer()

@dp.callback_query(F.data == "menu:help")
async def menu_help(call: CallbackQuery):
    await call.message.edit_text("ℹ️ <b>Правила</b>\n\n• Регистрация обязательна.\n• Минимальный депозит — <b>250 USDT</b>.\n• Сеть — <b>TRC20</b>.\n• Депозит подтверждает администратор.\n• При достижении 500 USDT требуется полный вывод.\n• Комиссия вывода — 4%.\n\nПример: <b>500 → 20 комиссии → 480 USDT пользователю.</b>",reply_markup=back_kb(locale_for(call.from_user.id))); await call.answer()

@dp.callback_query(F.data == "menu:history")
async def menu_history(call: CallbackQuery):
    code=locale_for(call.from_user.id)
    u=ensure_user(call.from_user)
    if not u:
        await call.answer(flow(code,"unregistered"),show_alert=True); return
    rows=db.table("balance_transactions").select("*").eq("user_id",u["id"]).order("created_at",desc=True).limit(8).execute().data or []
    if not rows:
        text=f"📜 <b>{flow(code,'history')}</b>\n\n{flow(code,'no_history')}"
    else:
        labels={"deposit":flow(code,"deposit"),"withdrawal":flow(code,"withdraw"),"adjustment":"Adjustment"}
        lines=[f"📜 <b>{flow(code,'history')}</b>",""]
        for x in rows:
            amount=Decimal(str(x["amount"]))
            sign="+" if amount>0 else ""
            date=str(x["created_at"]).replace("T"," ")[:16]
            lines.append(f"<code>{date}</code>  <b>{sign}{money(amount)} USDT</b>\n{labels.get(x['type'],x['type'])}")
        text="\n".join(lines)
    await call.message.edit_text(text,reply_markup=back_kb(code))
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
    await call.message.edit_text(text, reply_markup=back_kb(locale_for(call.from_user.id)))
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
    await message.answer(home_text(user),reply_markup=home_kb(locale_for(message.from_user.id)))

@dp.message(F.text == "💰 Мой баланс")
async def balance(message: Message):
    u = ensure_user(message.from_user)
    await message.answer(f"💰 <b>Ваш баланс</b>\n\n<code>{money(u['balance'])} ₸</code>", reply_markup=balance_kb(locale_for(message.from_user.id)))

@dp.message(F.text == "📜 История")
async def history(message: Message):
    u = ensure_user(message.from_user)
    r = db.table("balance_transactions").select("*").eq("user_id", u["id"]).order("created_at", desc=True).limit(10).execute()
    rows = r.data or []
    if not rows:
        await message.answer("📜 <b>История операций</b>\n\nПока операций нет.", reply_markup=back_kb(locale_for(message.from_user.id)))
        return
    lines = ["📜 <b>История операций</b>", ""]
    for x in rows:
        amount = Decimal(str(x["amount"]))
        sign = "+" if amount > 0 else ""
        lines.append(f"{str(x['created_at']).replace('T',' ')[:16]} — {sign}{money(amount)} ₸ — {x['type']}")
    await message.answer("\n".join(lines), reply_markup=back_kb(locale_for(message.from_user.id)))

@dp.message(F.text == "💳 Реквизиты")
async def wallets(message: Message):
    rows = db.table("wallets").select("*").eq("is_active", True).order("sort_order").execute().data or []
    if not rows:
        await message.answer("💳 <b>Реквизиты</b>\n\n⚠️ Активных реквизитов сейчас нет.", reply_markup=back_kb(locale_for(message.from_user.id)))
        return
    text = "💳 <b>Реквизиты для пополнения</b>\n"
    for x in rows:
        text += f"\n<b>{x['title']}</b>\n{x['bank_name']}\n<code>{x['requisites']}</code>\nПолучатель: {x.get('holder_name') or '—'}\n"
    await message.answer(text, reply_markup=back_kb(locale_for(message.from_user.id)))

@dp.message(F.text == "🌐 Язык")
async def language(message: Message):
    await message.answer("🌐 <b>Язык</b>\n\nСейчас доступен русский язык.", reply_markup=back_kb(locale_for(message.from_user.id)))

@dp.message(F.text == "💳 Пополнить баланс")
async def deposit_start(message: Message, state: FSMContext):
    await start_deposit_flow(message, state, message.from_user)

@dp.message(Deposit.amount)
async def deposit_amount(message:Message,state:FSMContext):
    code=locale_for(message.from_user.id)
    try: amount=Decimal((message.text or "").replace(" ","").replace(",",".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer(flow(code,"invalid_amount")); return
    if amount<MIN_DEPOSIT:
        await message.answer(f"❌ {flow(code,'minimum')}: <b>{money(MIN_DEPOSIT)} USDT</b>."); return
    u=ensure_user(message.from_user)
    if not u:
        await state.clear(); await message.answer(flow(code,"unregistered"),reply_markup=auth_kb(code)); return
    if Decimal(str(u["balance"]))>=THRESHOLD:
        await state.clear(); await message.answer(f"⚠️ {flow_extra(code,'unavailable')}",reply_markup=home_kb(code)); return
    try:
        o=db.table("orders").insert({"user_id":u["id"],"wallet_id":None,"wallet_snapshot":{"network":"TRC20","asset":"USDT","address":ADMIN_TRC20_ADDRESS},"amount":float(amount),"currency":"USDT","network":"TRC20","status":"pending","expires_at":(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()}).execute().data[0]
    except Exception:
        logging.exception("deposit create failed"); await message.answer("❌ Could not create the request."); return
    await state.set_state(Deposit.txid); await state.update_data(order_id=o["id"])
    await message.answer(f"🧾 <b>#{o['order_number']}</b>\n\n{flow_extra(code,'amount_label')}: <b>{money(amount)} USDT</b>\n{flow_extra(code,'network_label')}: <b>TRC20</b>\n\n{flow_extra(code,'address_label')}:\n<code>{ADMIN_TRC20_ADDRESS}</code>\n\n{flow_extra(code,'txid_instruction')}",reply_markup=address_copy_kb(ADMIN_TRC20_ADDRESS,code))

@dp.message(Deposit.txid)
async def deposit_txid(message: Message, state: FSMContext):
    code=locale_for(message.from_user.id)
    order_id = (await state.get_data()).get("order_id")
    if not order_id:
        await state.clear()
        await message.answer(flow(code,"order_missing"), reply_markup=home_kb(code))
        return

    rows = db.table("orders").select("*").eq("id", order_id).limit(1).execute().data
    if not rows:
        await state.clear()
        await message.answer(flow(code,"order_missing"), reply_markup=home_kb(code))
        return

    receipt = None
    update = {"status": "under_review", "updated_at": now()}
    if message.text:
        txid = message.text.strip()
        if len(txid) < 20:
            await message.answer(flow_extra(code,"txid_invalid"))
            return
        update["deposit_tx_hash"] = txid
    elif message.photo:
        receipt = ("photo", message.photo[-1].file_id)
    elif message.document:
        receipt = ("document", message.document.file_id)
    else:
        await message.answer(flow_extra(code,"receipt_instruction"))
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
        flow_extra(code,"receipt_pending"),
        reply_markup=home_kb(locale_for(message.from_user.id)),
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
        await notify_user(int(result["telegram_id"]), f"{flow_extra(locale_for(int(result['telegram_id'])),'paid')}\n\n{flow_extra(locale_for(int(result['telegram_id'])),'amount_label')}: <b>{money(result['amount'])} USDT</b>\n{flow(locale_for(int(result['telegram_id'])),'balance')}: <b>{money(result['balance'])} USDT</b>")
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
        await notify_user(int(u[0]["telegram_id"]), f"{flow_extra(locale_for(int(u[0]['telegram_id'])),'rejected_user')} #{o['order_number']}.")
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
    code=locale_for(message.from_user.id)
    addr=(message.text or "").strip()
    if not valid_tron_address(addr): await message.answer(flow(code,"invalid_tron")); return
    u=ensure_user(message.from_user)
    if not u: await state.clear(); await message.answer(flow(code,"unregistered"),reply_markup=auth_kb(code)); return
    bal=Decimal(str(u["balance"]))
    if bal<THRESHOLD: await state.clear(); await message.answer(f"⚠️ {flow(code,'balance')} changed.",reply_markup=home_kb(code)); return
    fee=(bal*FEE_RATE).quantize(Decimal("0.01"),rounding=ROUND_DOWN); net=bal-fee
    try:
        wd=db.table("withdrawals").insert({"user_id":u["id"],"amount":float(bal),"fee_amount":float(fee),"net_amount":float(net),"currency":"USDT","destination_address":addr,"status":"pending","note":"Mandatory full withdrawal; 4% fee retained by admin."}).execute().data[0]
    except Exception:
        logging.exception("withdrawal create failed"); await message.answer("❌ Could not create the request."); return
    await state.clear()
    await message.answer(f"📤 <b>{flow_extra(code,'withdraw_created')}</b>\n\n{flow(code,'balance')}: <b>{money(bal)} USDT</b>\n{flow(code,'fee')}: <b>{money(fee)} USDT</b>\n{flow(code,'receive')}: <b>{money(net)} USDT</b>\n\n{flow_extra(code,'address_label')}:\n<code>{addr}</code>",reply_markup=home_kb(code))
    await notify_withdrawal_admins(wd["id"])

async def notify_withdrawal_admins(wid):
    rows=db.table("withdrawals").select("*").eq("id",wid).limit(1).execute().data
    if not rows:return
    w=rows[0]; urows=db.table("bot_users").select("*").eq("id",w["user_id"]).limit(1).execute().data; u=urows[0] if urows else {}
    b=InlineKeyboardBuilder(); b.button(text="✅ Выплачено / подтвердить",callback_data=f"wconfirm:{wid}"); b.button(text="❌ Отклонить",callback_data=f"wreject:{wid}"); b.adjust(1)
    text=f"📤 <b>Вывод</b>\n\nКлиент: @{u.get('username') or 'без_username'}\nСписать: <b>{money(w['amount'])} USDT</b>\nКомиссия: <b>{money(w['fee_amount'])} USDT</b>\nОтправить: <b>{money(w['net_amount'])} USDT</b>\nАдрес: <code>{w['destination_address']}</code>\n\nПосле ручной отправки нажмите кнопку подтверждения."
    for aid in admin_ids(): await notify_user(aid,text); await bot.send_message(aid,"Подтвердите выплату:",reply_markup=b.as_markup())

@dp.callback_query(F.data.startswith("wconfirm:"))
async def wconfirm(call:CallbackQuery):
    code=locale_for(call.from_user.id)
    if not is_admin(call.from_user.id): await call.answer("Нет доступа",show_alert=True); return
    wid=call.data.split(":",1)[1]; result=db.rpc("confirm_withdrawal",{"p_withdrawal_id":wid,"p_admin_telegram_id":call.from_user.id}).execute().data or {}
    if result.get("ok") is not True: await call.answer(f"Ошибка: {result.get('error','unknown')}",show_alert=True); return
    await notify_user(int(result["telegram_id"]),f"{flow_extra(locale_for(int(result['telegram_id'])),'withdraw_paid')}\n\n{flow(code if 'code' in locals() else locale_for(int(result['telegram_id'])),'receive')}: <b>{money(result['net_amount'])} USDT</b>\n{flow(locale_for(int(result['telegram_id'])),'fee')}: <b>{money(result['fee_amount'])} USDT</b>"); await call.message.edit_text("✅ <b>Вывод подтверждён. Баланс списан.</b>"); await call.answer("Готово")

@dp.callback_query(F.data.startswith("wreject:"))
async def wreject(call:CallbackQuery):
    if not is_admin(call.from_user.id): await call.answer("Нет доступа",show_alert=True); return
    wid=call.data.split(":",1)[1]; rows=db.table("withdrawals").select("*").eq("id",wid).limit(1).execute().data
    if not rows: await call.answer("Заявка не найдена",show_alert=True); return
    w=rows[0]
    if w["status"]=="paid": await call.answer("Уже выплачено",show_alert=True); return
    db.table("withdrawals").update({"status":"rejected","processed_at":now(),"processed_by":call.from_user.id,"note":"Rejected by admin; balance unchanged."}).eq("id",wid).execute(); u=db.table("bot_users").select("telegram_id").eq("id",w["user_id"]).limit(1).execute().data
    if u: await notify_user(int(u[0]["telegram_id"]),flow_extra(locale_for(int(u[0]["telegram_id"])),"withdraw_rejected"))
    await call.message.edit_text("❌ <b>Вывод отклонён.</b>"); await call.answer()

@dp.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ <b>Операция отменена.</b>", reply_markup=home_kb(locale_for(message.from_user.id)))

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
                    await notify_user(int(u[0]["telegram_id"]), f"{flow_extra(locale_for(int(u[0]['telegram_id'])),'expired')} #{o['order_number']}.")
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
    elif user: await call.message.edit_text(home_text(user),reply_markup=home_kb(locale_for(call.from_user.id)))
    else: await call.message.edit_text(auth_screen(code),reply_markup=auth_kb(code))


@dp.callback_query(F.data == "auth:docs")
async def auth_documentation(call:CallbackQuery):
    await call.answer()
    await call.message.edit_text(docs_screen(locale_for(call.from_user.id)),reply_markup=back_kb(locale_for(call.from_user.id)))


@dp.callback_query(F.data == "auth:login")
async def auth_login(call:CallbackQuery,state:FSMContext):
    await call.answer()
    user=ensure_user(call.from_user)
    if user:
        await call.message.edit_text(home_text(user),reply_markup=home_kb(locale_for(call.from_user.id))); return
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
        await message.answer(f"{auth_prompt(code,'success')}\n\n{home_text(user)}",reply_markup=home_kb(locale_for(message.from_user.id))); return
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
