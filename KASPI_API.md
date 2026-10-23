# Kaspi receipt API

Base URL: `https://<Railway-domain>/api/v1`. All routes except `/health` require `Authorization: Bearer <API_TOKEN>` and HTTPS. JSON is UTF-8.

## Submit a receipt

`POST /receipts`

Required header: `Idempotency-Key: <unique request key>`.

```json
{
  "external_receipt_id": "client-bot-12345",
  "amount": 12500,
  "currency": "KZT",
  "submitted_at": "2026-10-23T12:30:00+05:00",
  "customer_reference": "optional-customer-reference",
  "receipt": {
    "type": "file_id",
    "media_type": "photo",
    "value": "telegram-file-id"
  },
  "metadata": {"source": "client_bot"}
}
```

`receipt.type` can be `file_id` or `file_url`. A URL must use HTTPS. For a Telegram file ID, `media_type` is `photo` or `document`.

The API returns `201` for a new request and `200` for a repeated idempotency key. The same receipt ID is retained on retries.

## Read a receipt

`GET /receipts/{receipt_id}`

Returns status, submitted and approved amounts, currency, and timestamps.

## Decide a receipt

`POST /receipts/{receipt_id}/decision`

```json
{"decision":"confirmed","approved_amount":12000,"operator_id":123456789,"comment":"Checked in Kaspi"}
```

Or reject:

```json
{"decision":"rejected","operator_id":123456789,"reason":"Receipt not confirmed"}
```

Only IDs listed in `OPERATOR_IDS` can decide requests. Concurrent or repeated decisions cannot create a second ledger credit.

## Balance and history

- `GET /balance` — confirmed total in KZT; this is an internal ledger, not a live bank balance.
- `GET /balance/transactions?limit=50&cursor=<optional>` — paginated confirmed credits.

## Health

`GET /health` returns `{"ok":true,"service":"kaspi-receipts"}` and does not expose secrets.

