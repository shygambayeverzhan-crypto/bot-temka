import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.client.default import DefaultBotProperties
from supabase import Client, create_client


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kaspi-receipts")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
API_TOKEN = os.getenv("API_TOKEN", "").strip()
CLIENT_WEBHOOK_URL = os.getenv("CLIENT_WEBHOOK_URL", "").strip()
CLIENT_WEBHOOK_SECRET = os.getenv("CLIENT_WEBHOOK_SECRET", "").strip()

# Add operator Telegram IDs here when they are ready, for example {123456789}.
OPERATOR_IDS: set[int] = set()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")

db: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


class ReceiptSubmission(StatesGroup):
    amount = State()
    receipt = State()


class OtherAmount(StatesGroup):
    amount = State()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def amount_value(raw: str | None) -> Decimal | None:
    try:
        value = Decimal((raw or "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def receipt_keyboard(receipt_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"receipt:confirm:{receipt_id}")],
        [InlineKeyboardButton(text="✏️ Подтвердить другую сумму", callback_data=f"receipt:other:{receipt_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"receipt:reject:{receipt_id}")],
    ])


def require_operator(user_id: int) -> bool:
    return user_id in OPERATOR_IDS


def fetch_receipt(receipt_id: str) -> dict | None:
    rows = db.table("kaspi_receipts").select("*").eq("id", receipt_id).limit(1).execute().data or []
    return rows[0] if rows else None


async def send_receipt_to_operators(row: dict, reminder: bool = False) -> bool:
    if not OPERATOR_IDS:
        log.warning("receipt %s is saved; OPERATOR_IDS is empty", row["id"])
        return False
    who = row.get("customer_reference") or str(row.get("telegram_user_id") or "без ссылки")
    caption = (
        f"🧾 <b>Чек Kaspi {('— напоминание' if reminder else '')}</b>\n"
        f"Номер: <code>{row['id']}</code>\n"
        f"Сумма от пользователя: <b>{row['amount']} {row['currency']}</b>\n"
        f"Клиент: <code>{who}</code>\n"
        f"Статус: {row['status']}"
    )
    keyboard = receipt_keyboard(row["id"])
    delivered = 0
    for operator_id in OPERATOR_IDS:
        try:
            if row.get("receipt_type") == "file_id":
                if row["receipt_value"].startswith("photo:"):
                    await bot.send_photo(operator_id, row["receipt_value"][6:], caption=caption, reply_markup=keyboard)
                elif row["receipt_value"].startswith("document:"):
                    await bot.send_document(operator_id, row["receipt_value"][9:], caption=caption, reply_markup=keyboard)
                else:
                    await bot.send_message(operator_id, caption, reply_markup=keyboard)
            elif row.get("receipt_type") == "file_url":
                await bot.send_message(operator_id, caption + f"\nФайл: {row['receipt_value']}", reply_markup=keyboard)
            else:
                await bot.send_message(operator_id, caption, reply_markup=keyboard)
            delivered += 1
        except Exception as exc:
            log.exception("could not notify operator for receipt %s", row["id"])
            try:
                db.table("kaspi_receipts").update({"last_notification_error": str(exc)[:500]}).eq("id", row["id"]).execute()
            except Exception:
                log.exception("could not record operator notification failure")
    return delivered == len(OPERATOR_IDS)


def create_receipt(payload: dict) -> dict:
    existing = db.table("kaspi_receipts").select("id").eq("idempotency_key", payload["idempotency_key"]).limit(1).execute().data or []
    if not existing and payload.get("external_receipt_id"):
        existing = db.table("kaspi_receipts").select("id").eq("external_receipt_id", payload["external_receipt_id"]).limit(1).execute().data or []
    result = db.rpc("create_kaspi_receipt", {
        "p_external_receipt_id": payload.get("external_receipt_id"),
        "p_idempotency_key": payload["idempotency_key"],
        "p_amount": payload["amount"],
        "p_currency": payload.get("currency", "KZT"),
        "p_submitted_at": payload.get("submitted_at"),
        "p_customer_reference": payload.get("customer_reference"),
        "p_receipt_type": payload["receipt_type"],
        "p_receipt_value": payload["receipt_value"],
        "p_telegram_user_id": payload.get("telegram_user_id"),
        "p_metadata": payload.get("metadata") or {},
    }).execute().data
    if isinstance(result, list) and result:
        row = result[0]
        row["_created"] = not bool(existing)
        return row
    if isinstance(result, dict) and "id" in result:
        result["_created"] = not bool(existing)
        return result
    raise RuntimeError("receipt insert returned no row")


def decide_receipt(receipt_id: str, decision: str, operator_id: int,
                   approved_amount: Decimal | None = None, comment: str = "") -> dict:
    result = db.rpc("decide_kaspi_receipt", {
        "p_receipt_id": receipt_id,
        "p_decision": decision,
        "p_approved_amount": str(approved_amount) if approved_amount is not None else None,
        "p_operator_id": operator_id,
        "p_comment": comment,
    }).execute().data
    if isinstance(result, list):
        return result[0] if result else {}
    return result or {}


async def send_client_webhook(row: dict) -> None:
    if not CLIENT_WEBHOOK_URL:
        return
    event = {
        "event": "receipt.decision",
        "event_id": row.get("webhook_event_id") or str(uuid.uuid4()),
        "receipt_id": row["id"],
        "external_receipt_id": row.get("external_receipt_id"),
        "status": row["status"],
        "approved_amount": str(row["approved_amount"]) if row.get("approved_amount") is not None else None,
        "currency": row["currency"],
        "decided_at": row.get("decided_at"),
    }
    body = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    headers = {"Content-Type": "application/json"}
    if CLIENT_WEBHOOK_SECRET:
        signature = hmac.new(CLIENT_WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Receipt-Signature"] = f"sha256={signature}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(CLIENT_WEBHOOK_URL, content=body.encode(), headers=headers)
            response.raise_for_status()
        db.table("kaspi_receipts").update({"webhook_delivered_at": now_iso(), "webhook_event_id": event["event_id"], "webhook_attempts": (row.get("webhook_attempts") or 0) + 1, "webhook_last_error": None}).eq("id", row["id"]).execute()
    except Exception as exc:
        log.exception("client webhook failed for receipt %s", row["id"])
        try:
            retry_at = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
            db.table("kaspi_receipts").update({"webhook_event_id": event["event_id"], "webhook_attempts": (row.get("webhook_attempts") or 0) + 1, "webhook_last_error": str(exc)[:500], "webhook_next_attempt_at": retry_at}).eq("id", row["id"]).execute()
        except Exception:
            log.exception("could not record webhook error")


async def notify_customer(row: dict) -> None:
    chat_id = row.get("telegram_user_id")
    if not chat_id:
        return
    approved = row.get("approved_amount")
    if row["status"] == "confirmed":
        msg = f"✅ Чек подтверждён. Зачислено: <b>{approved} {row['currency']}</b>."
    else:
        msg = "❌ Чек отклонён. Если считаете, что это ошибка, свяжитесь с поддержкой."
    try:
        await bot.send_message(int(chat_id), msg)
    except Exception:
        log.exception("could not notify customer for receipt %s", row["id"])


async def post_decision_effects(receipt_id: str) -> None:
    row = fetch_receipt(receipt_id)
    if not row:
        return
    await notify_customer(row)
    if CLIENT_WEBHOOK_URL and not row.get("webhook_delivered_at"):
        await send_client_webhook(row)


async def reminder_loop() -> None:
    while True:
        try:
            rows = db.rpc("claim_due_kaspi_receipts", {"p_limit": 50}).execute().data or []
            for row in rows:
                await send_receipt_to_operators(row, reminder=True)
            if CLIENT_WEBHOOK_URL:
                due = db.table("kaspi_receipts").select("*").in_("status", ["confirmed", "rejected"]).is_("webhook_delivered_at", "null").not_.is_("webhook_next_attempt_at", "null").lte("webhook_next_attempt_at", now_iso()).limit(25).execute().data or []
                for row in due:
                    await send_client_webhook(row)
        except Exception:
            log.exception("background receipt reminder/webhook loop failed")
        await asyncio.sleep(15)


def api_authorized(request: web.Request) -> bool:
    if not API_TOKEN:
        return False
    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {API_TOKEN}"
    return hmac.compare_digest(supplied, expected)


@web.middleware
async def api_auth_middleware(request: web.Request, handler):
    if request.path == "/health":
        return await handler(request)
    if not api_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def health(_request):
    return web.json_response({"ok": True, "service": "kaspi-receipts"})


async def api_create_receipt(request: web.Request):
    try:
        data = await request.json()
        amount = amount_value(str(data.get("amount", "")))
        if amount is None or str(data.get("currency", "KZT")).upper() != "KZT":
            return web.json_response({"error": "amount must be positive and currency must be KZT"}, status=422)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return web.json_response({"error": "Idempotency-Key header is required"}, status=400)
        receipt = data.get("receipt") or {}
        receipt_type = receipt.get("type")
        receipt_value = str(receipt.get("value", "")).strip()
        if receipt_type not in {"file_id", "file_url"} or not receipt_value:
            return web.json_response({"error": "receipt.type must be file_id or file_url and value is required"}, status=400)
        if receipt_type == "file_url" and not receipt_value.startswith("https://"):
            return web.json_response({"error": "receipt URL must use HTTPS"}, status=422)
        if receipt_type == "file_id":
            media_type = receipt.get("media_type", "document")
            if media_type not in {"photo", "document"}:
                return web.json_response({"error": "receipt.media_type must be photo or document"}, status=422)
            receipt_value = f"{media_type}:{receipt_value}"
        payload = {
            "external_receipt_id": data.get("external_receipt_id"),
            "idempotency_key": idempotency_key,
            "amount": str(amount),
            "currency": "KZT",
            "submitted_at": data.get("submitted_at"),
            "customer_reference": data.get("customer_reference"),
            "receipt_type": receipt_type,
            "receipt_value": receipt_value,
            "metadata": data.get("metadata") or {},
        }
        row = await asyncio.to_thread(create_receipt, payload)
        if row["status"] == "pending" and not row.get("operator_notified_at"):
            await send_receipt_to_operators(row)
            db.table("kaspi_receipts").update({"operator_notified_at": now_iso()}).eq("id", row["id"]).execute()
        status = 201 if row.pop("_created", False) else 200
        return web.json_response({"receipt_id": row["id"], "status": row["status"], "next_reminder_at": row.get("next_reminder_at")}, status=status)
    except Exception:
        log.exception("API receipt create failed")
        return web.json_response({"error": "could not create receipt"}, status=500)


async def api_get_receipt(request: web.Request):
    try:
        row = await asyncio.to_thread(fetch_receipt, request.match_info["receipt_id"])
        if not row:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({
            "receipt_id": row["id"], "status": row["status"], "submitted_amount": row["amount"],
            "approved_amount": row.get("approved_amount"), "currency": row["currency"],
            "decision": row.get("decision"), "created_at": row["created_at"], "decided_at": row.get("decided_at"),
        })
    except Exception:
        log.exception("API receipt lookup failed")
        return web.json_response({"error": "could not get receipt"}, status=500)


async def api_decide_receipt(request: web.Request):
    try:
        data = await request.json()
        decision = data.get("decision")
        operator_id = int(data.get("operator_id", 0))
        if decision not in {"confirmed", "rejected"} or not require_operator(operator_id):
            return web.json_response({"error": "invalid decision or operator"}, status=422)
        approved = amount_value(str(data.get("approved_amount", ""))) if decision == "confirmed" else None
        if decision == "confirmed" and approved is None:
            return web.json_response({"error": "approved_amount must be positive"}, status=422)
        result = await asyncio.to_thread(decide_receipt, request.match_info["receipt_id"], decision, operator_id, approved, str(data.get("comment") or ""))
        if result.get("ok") and not result.get("already_decided"):
            await post_decision_effects(request.match_info["receipt_id"])
        code = 404 if result.get("error") == "not_found" else 409 if result.get("already_decided") else 422 if not result.get("ok") else 200
        return web.json_response(result, status=code)
    except Exception:
        log.exception("API receipt decision failed")
        return web.json_response({"error": "could not decide receipt"}, status=500)


async def api_balance(_request: web.Request):
    try:
        result = db.rpc("kaspi_balance_summary").execute().data
        row = result[0] if isinstance(result, list) and result else result
        return web.json_response(row or {"currency": "KZT", "confirmed_total": 0, "updated_at": None, "balance_type": "internal_ledger"})
    except Exception:
        log.exception("API balance lookup failed")
        return web.json_response({"error": "could not get balance"}, status=500)


async def api_transactions(request: web.Request):
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 100))
        query = db.table("kaspi_balance_transactions").select("*").order("created_at", desc=True).limit(limit)
        cursor = request.query.get("cursor")
        if cursor:
            raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
            created_at, transaction_id = raw["created_at"], raw["id"]
            query = query.or_(f"created_at.lt.{created_at},and(created_at.eq.{created_at},id.lt.{transaction_id})")
        rows = query.execute().data or []
        next_cursor = base64.urlsafe_b64encode(json.dumps({"created_at": rows[-1]["created_at"], "id": rows[-1]["id"]}).encode()).decode().rstrip("=") if len(rows) == limit else None
        return web.json_response({"items": rows, "next_cursor": next_cursor})
    except Exception:
        log.exception("API transaction history failed")
        return web.json_response({"error": "could not get transactions"}, status=500)


def api_app() -> web.Application:
    app = web.Application(middlewares=[api_auth_middleware], client_max_size=1024 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/api/v1/receipts", api_create_receipt)
    app.router.add_get("/api/v1/receipts/{receipt_id}", api_get_receipt)
    app.router.add_post("/api/v1/receipts/{receipt_id}/decision", api_decide_receipt)
    app.router.add_get("/api/v1/balance", api_balance)
    app.router.add_get("/api/v1/balance/transactions", api_transactions)
    return app


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Здравствуйте! Здесь можно отправить чек Kaspi на проверку.\n"
        "Нажмите /receipt, укажите сумму в KZT и приложите фото или файл чека.\n"
        "Статус заявки придёт сюда после проверки."
    )


@dp.message(Command("receipt"))
async def receipt_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ReceiptSubmission.amount)
    await message.answer("Введите сумму по чеку в KZT, например 12500:")


@dp.message(ReceiptSubmission.amount)
async def receipt_amount(message: Message, state: FSMContext):
    amount = amount_value(message.text)
    if amount is None:
        await message.answer("Введите положительную сумму, например 12500:")
        return
    await state.update_data(amount=str(amount))
    await state.set_state(ReceiptSubmission.receipt)
    await message.answer("Теперь отправьте фото или файл чека Kaspi:")


@dp.message(ReceiptSubmission.receipt)
async def receipt_file(message: Message, state: FSMContext):
    if message.photo:
        kind, value = "file_id", "photo:" + message.photo[-1].file_id
    elif message.document:
        kind, value = "file_id", "document:" + message.document.file_id
    else:
        await message.answer("Нужны фото или файл чека. Отправьте его сообщением:")
        return
    data = await state.get_data()
    key = f"telegram:{message.from_user.id}:{message.message_id}"
    payload = {
        "external_receipt_id": key,
        "idempotency_key": key,
        "amount": data["amount"],
        "currency": "KZT",
        "submitted_at": now_iso(),
        "customer_reference": f"telegram:{message.from_user.id}",
        "receipt_type": kind,
        "receipt_value": value,
        "telegram_user_id": message.from_user.id,
        "metadata": {"source": "telegram_bot", "username": message.from_user.username or ""},
    }
    try:
        row = await asyncio.to_thread(create_receipt, payload)
        await send_receipt_to_operators(row)
        db.table("kaspi_receipts").update({"operator_notified_at": now_iso()}).eq("id", row["id"]).execute()
    except Exception:
        log.exception("telegram receipt submission failed")
        await message.answer("Не получилось сохранить чек. Попробуйте позже или обратитесь к администратору.")
        return
    await state.clear()
    await message.answer(f"✅ Чек принят. Номер заявки: <code>{row['id']}</code>. Статус: ожидает проверки.")


@dp.message(Command("queue"))
async def operator_queue(message: Message):
    if not require_operator(message.from_user.id):
        await message.answer("Нет доступа к очереди операторов.")
        return
    rows = db.table("kaspi_receipts").select("*").eq("status", "pending").order("created_at").limit(50).execute().data or []
    if not rows:
        await message.answer("Очередь чеков пуста.")
        return
    for row in rows:
        await send_receipt_to_operators(row)


@dp.message(Command("balance"))
async def operator_balance(message: Message):
    if not require_operator(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    result = db.rpc("kaspi_balance_summary").execute().data
    row = result[0] if isinstance(result, list) and result else result or {}
    await message.answer(f"Внутренний баланс: <b>{row.get('confirmed_total', 0)} KZT</b>")


@dp.callback_query(F.data.startswith("receipt:confirm:"))
async def operator_confirm(call: CallbackQuery):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    receipt_id = call.data.rsplit(":", 1)[1]
    row = fetch_receipt(receipt_id)
    if not row:
        await call.answer("Чек не найден.", show_alert=True)
        return
    result = await asyncio.to_thread(decide_receipt, receipt_id, "confirmed", call.from_user.id, Decimal(str(row["amount"])))
    if result.get("ok") and not result.get("already_decided"):
        await post_decision_effects(receipt_id)
        await call.answer("Подтверждено.")
        await call.message.edit_reply_markup(reply_markup=None)
    else:
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)


@dp.callback_query(F.data.startswith("receipt:reject:"))
async def operator_reject(call: CallbackQuery):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    receipt_id = call.data.rsplit(":", 1)[1]
    result = await asyncio.to_thread(decide_receipt, receipt_id, "rejected", call.from_user.id)
    if result.get("ok") and not result.get("already_decided"):
        await post_decision_effects(receipt_id)
        await call.answer("Отклонено.")
        await call.message.edit_reply_markup(reply_markup=None)
    else:
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)


@dp.callback_query(F.data.startswith("receipt:other:"))
async def operator_other_amount(call: CallbackQuery, state: FSMContext):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    receipt_id = call.data.rsplit(":", 1)[1]
    row = fetch_receipt(receipt_id)
    if not row or row["status"] != "pending":
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)
        return
    await state.set_state(OtherAmount.amount)
    await state.update_data(receipt_id=receipt_id, review_message_id=call.message.message_id, review_chat_id=call.message.chat.id)
    await call.message.answer(f"Введите фактически подтверждённую сумму в KZT для чека <code>{receipt_id}</code>:")
    await call.answer()


@dp.message(OtherAmount.amount)
async def operator_set_other_amount(message: Message, state: FSMContext):
    if not require_operator(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    amount = amount_value(message.text)
    if amount is None:
        await message.answer("Введите положительную сумму в KZT:")
        return
    data = await state.get_data()
    result = await asyncio.to_thread(decide_receipt, data["receipt_id"], "confirmed", message.from_user.id, amount, "Confirmed with a different amount")
    if result.get("ok") and not result.get("already_decided"):
        await post_decision_effects(data["receipt_id"])
        await message.answer(f"✅ Заявка {data['receipt_id']} подтверждена на {amount} KZT.")
        try:
            await bot.edit_message_reply_markup(chat_id=data["review_chat_id"], message_id=data["review_message_id"], reply_markup=None)
        except Exception:
            log.exception("could not clear processed receipt buttons")
    else:
        await message.answer("Заявка уже обработана или не найдена.")
    await state.clear()


async def main():
    if not API_TOKEN:
        log.warning("API_TOKEN is not set; REST API requests will return 401")
    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(api_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    asyncio.create_task(reminder_loop())
    log.info("Kaspi receipt service listening on port %s", port)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

