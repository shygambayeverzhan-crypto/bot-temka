import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

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
OPERATOR_IDS: set[int] = {6575928505}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")

db: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


class OtherAmount(StatesGroup):
    amount = State()
    confirm = State()


class RejectReason(StatesGroup):
    reason = State()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def amount_value(raw: str | None) -> Decimal | None:
    try:
        value = Decimal((raw or "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


async def receipt_url_is_accessible(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443, 0, socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            return False
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            async with client.stream("GET", value, headers={"Range": "bytes=0-0"}) as response:
                return 200 <= response.status_code < 300
    except Exception:
        log.exception("receipt URL preflight failed")
        return False


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
        "comment": row.get("comment"),
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


async def post_decision_effects(receipt_id: str) -> None:
    row = fetch_receipt(receipt_id)
    if not row:
        return
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
        external_receipt_id = str(data.get("external_receipt_id", "")).strip()
        if not external_receipt_id:
            return web.json_response({"error": "external_receipt_id is required"}, status=400)
        receipt = data.get("receipt") or {}
        receipt_type = receipt.get("type")
        receipt_value = str(receipt.get("value", "")).strip()
        if receipt_type != "file_url" or not receipt_value:
            return web.json_response({"error": "receipt.type must be file_url and value is required"}, status=400)
        if not receipt_value.startswith("https://"):
            return web.json_response({"error": "receipt URL must use HTTPS"}, status=422)
        if not await receipt_url_is_accessible(receipt_value):
            return web.json_response({"error": "receipt URL is not publicly accessible over HTTPS"}, status=422)
        payload = {
            "external_receipt_id": external_receipt_id,
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
        return web.json_response({"receipt_id": row["id"], "external_receipt_id": row.get("external_receipt_id"), "status": row["status"], "next_reminder_at": row.get("next_reminder_at")}, status=status)
    except Exception:
        log.exception("API receipt create failed")
        return web.json_response({"error": "could not create receipt"}, status=500)


async def api_get_receipt(request: web.Request):
    try:
        row = await asyncio.to_thread(fetch_receipt, request.match_info["receipt_id"])
        if not row:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({
            "receipt_id": row["id"], "external_receipt_id": row.get("external_receipt_id"), "status": row["status"], "submitted_amount": row["amount"],
            "approved_amount": row.get("approved_amount"), "currency": row["currency"],
            "decision": row.get("decision"), "comment": row.get("comment"), "created_at": row["created_at"], "decided_at": row.get("decided_at"),
        })
    except Exception:
        log.exception("API receipt lookup failed")
        return web.json_response({"error": "could not get receipt"}, status=500)


def api_app() -> web.Application:
    app = web.Application(middlewares=[api_auth_middleware], client_max_size=1024 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/api/v1/receipts", api_create_receipt)
    app.router.add_get("/api/v1/receipts/{receipt_id}", api_get_receipt)
    return app


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if require_operator(message.from_user.id):
        await message.answer("Бот операторской проверки Kaspi. Используйте /queue для очереди и /balance для общего учётного баланса.")
    else:
        await message.answer("Этот бот доступен только операторам.")


@dp.message(Command("queue"))
async def operator_queue(message: Message):
    if not require_operator(message.from_user.id):
        await message.answer("Нет доступа к очереди операторов.")
        return
    rows = db.table("kaspi_receipts").select("*").in_("status", ["pending", "amount_review"]).order("created_at").limit(50).execute().data or []
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
    if not row or row["status"] not in {"pending", "amount_review"}:
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
async def operator_reject(call: CallbackQuery, state: FSMContext):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    receipt_id = call.data.rsplit(":", 1)[1]
    row = fetch_receipt(receipt_id)
    if not row or row["status"] not in {"pending", "amount_review"}:
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)
        return
    await state.set_state(RejectReason.reason)
    await state.update_data(receipt_id=receipt_id, review_message_id=call.message.message_id, review_chat_id=call.message.chat.id)
    skip_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отклонить без причины", callback_data=f"receipt:rejectskip:{receipt_id}")]
    ])
    await call.message.answer(f"Напишите причину отклонения заявки <code>{receipt_id}</code> или нажмите кнопку ниже:", reply_markup=skip_markup)
    await call.answer()


@dp.callback_query(F.data.startswith("receipt:rejectskip:"))
async def operator_reject_skip(call: CallbackQuery, state: FSMContext):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    receipt_id = call.data.rsplit(":", 1)[1]
    result = await asyncio.to_thread(decide_receipt, receipt_id, "rejected", call.from_user.id)
    if result.get("ok") and not result.get("already_decided"):
        await post_decision_effects(receipt_id)
        await state.clear()
        await call.answer("Отклонено.")
        await call.message.edit_reply_markup(reply_markup=None)
    else:
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)


@dp.message(RejectReason.reason)
async def operator_reject_with_reason(message: Message, state: FSMContext):
    if not require_operator(message.from_user.id):
        await state.clear()
        await message.answer("Нет доступа.")
        return
    data = await state.get_data()
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Напишите короткую причину отклонения текстом:")
        return
    result = await asyncio.to_thread(decide_receipt, data["receipt_id"], "rejected", message.from_user.id, None, reason[:500])
    if result.get("ok") and not result.get("already_decided"):
        await post_decision_effects(data["receipt_id"])
        await message.answer(f"Заявка {data['receipt_id']} отклонена. Причина сохранена.")
        try:
            await bot.edit_message_reply_markup(chat_id=data["review_chat_id"], message_id=data["review_message_id"], reply_markup=None)
        except Exception:
            log.exception("could not clear processed receipt buttons")
    else:
        await message.answer("Заявка уже обработана или не найдена.")
    await state.clear()


@dp.callback_query(F.data.startswith("receipt:other:"))
async def operator_other_amount(call: CallbackQuery, state: FSMContext):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    receipt_id = call.data.rsplit(":", 1)[1]
    row = fetch_receipt(receipt_id)
    if not row or row["status"] not in {"pending", "amount_review"}:
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)
        return
    changed = db.table("kaspi_receipts").update({"status": "amount_review"}).eq("id", receipt_id).in_("status", ["pending", "amount_review"]).select("id").execute().data or []
    if not changed:
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
    await state.update_data(proposed_amount=str(amount))
    await state.set_state(OtherAmount.confirm)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить сумму", callback_data=f"receipt:otherconfirm:{data['receipt_id']}")],
        [InlineKeyboardButton(text="↩️ Вернуться в очередь", callback_data=f"receipt:othercancel:{data['receipt_id']}")],
    ])
    await message.answer(f"Зачислить <b>{amount} KZT</b> по заявке <code>{data['receipt_id']}</code>?", reply_markup=markup)


@dp.callback_query(F.data.startswith("receipt:otherconfirm:"))
async def operator_confirm_other_amount(call: CallbackQuery, state: FSMContext):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    data = await state.get_data()
    receipt_id = call.data.rsplit(":", 1)[1]
    if data.get("receipt_id") != receipt_id or not data.get("proposed_amount"):
        await call.answer("Сумма подтверждения устарела. Начните действие заново.", show_alert=True)
        return
    amount = Decimal(data["proposed_amount"])
    result = await asyncio.to_thread(decide_receipt, receipt_id, "confirmed", call.from_user.id, amount, "Confirmed with a different amount")
    if result.get("ok") and not result.get("already_decided"):
        await post_decision_effects(receipt_id)
        await call.answer("Сумма подтверждена.")
        await call.message.edit_reply_markup(reply_markup=None)
        try:
            await bot.edit_message_reply_markup(chat_id=data["review_chat_id"], message_id=data["review_message_id"], reply_markup=None)
        except Exception:
            log.exception("could not clear processed receipt buttons")
    else:
        await call.answer("Заявка уже обработана или не найдена.", show_alert=True)
    await state.clear()


@dp.callback_query(F.data.startswith("receipt:othercancel:"))
async def operator_cancel_other_amount(call: CallbackQuery, state: FSMContext):
    if not require_operator(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    data = await state.get_data()
    receipt_id = call.data.rsplit(":", 1)[1]
    if data.get("receipt_id") == receipt_id:
        db.table("kaspi_receipts").update({"status": "pending"}).eq("id", receipt_id).eq("status", "amount_review").execute()
        await state.clear()
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Заявка осталась в очереди.")
    else:
        await call.answer("Сессия устарела. Заявка доступна в очереди.", show_alert=True)


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

