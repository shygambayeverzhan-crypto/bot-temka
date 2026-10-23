# Operator bot and receipt API

This service is bot №2 for operators. It does not replace or require access to any client bot. A separate client application can submit applications later through this API.

Base URL: `https://telegram-payment-bot-v2-production.up.railway.app/api/v1`. All routes except `/health` require `Authorization: Bearer <API_TOKEN>` over HTTPS. `API_TOKEN` is a server-to-server secret; it is separate from Telegram `BOT_TOKEN`.

## Submit a receipt

`POST /receipts`

Required headers: `Authorization: Bearer <API_TOKEN>` and `Idempotency-Key: <unique request key>`. `external_receipt_id` is required and must be the client's stable unique request number. Retries with the same key return the same application.

```json
{
  "external_receipt_id": "KSP-10452",
  "amount": 12500,
  "currency": "KZT",
  "submitted_at": "2026-10-23T12:30:00+05:00",
  "customer_reference": "client-123",
  "receipt": {
    "type": "file_url",
    "value": "https://files.example.com/signed/receipt.jpg"
  },
  "metadata": {"source": "client_bot"}
}
```

The file URL must be HTTPS and remain accessible to operators while the application is pending. A protected HTTPS URL is required because Telegram file IDs from another bot cannot be opened by bot №2.

The API returns `201` for a new application and `200` for a retry. The response includes the internal `receipt_id`, the caller's `external_receipt_id`, status, and next reminder time.

## Receipt status

- `pending` — waiting for operator review; repeated every two minutes.
- `amount_review` — an operator is entering a different amount; remains in the shared queue and continues to receive reminders.
- `confirmed` — approved amount is entered in the shared ledger once.
- `rejected` — not credited; an optional reason is recorded.

## Read status and receive decisions

`GET /receipts/{receipt_id}` returns the application status, submitted and approved amounts, external ID, decision comment, and timestamps. The client application can poll this route.

After an operator decides, the service can POST a `receipt.decision` event to `CLIENT_WEBHOOK_URL` if that variable is configured. The client bot/application owner must provide and operate that HTTPS endpoint. Events are signed with `CLIENT_WEBHOOK_SECRET` when set; failed deliveries are retried. Without a webhook, polling the status route remains available.

## Operator actions

Operators receive a shared queue in Telegram with **Confirm**, **Reject**, and **Different amount** actions. Reject asks for a reason or allows rejection without one. Different amount requires a second confirmation before the amount is credited. A database transaction prevents duplicate decisions and duplicate credits.

Only the Telegram IDs in `OPERATOR_IDS` can process receipts. Each operator must open bot №2 and press Start once so Telegram allows the bot to message them.

The API token is for receipt submission and status lookup. Decisions are made only by authorized operators in Telegram. The shared balance is available to operators with `/balance`; it is an internal accounting balance, not a live Kaspi bank balance.

## Health

`GET /health` returns `{"ok":true,"service":"kaspi-receipts"}` and does not expose secrets.

