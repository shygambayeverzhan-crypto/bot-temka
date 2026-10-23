# Kaspi receipt review bot

This service replaces the previous USDT deposit and withdrawal bot workflow with a KZT receipt review queue. It accepts receipts in Telegram and through the protected API, lets configured operators confirm or reject them, and adds each confirmed amount to an internal KZT ledger once.

The internal balance is the sum of confirmed receipts. It is not a Kaspi bank account balance.

## Railway setup

The active Railway service should start with `python -u kaspi_bot.py` and expose port `8080` (Railway supplies `PORT`). Configure these Railway variables:

- `BOT_TOKEN` — Telegram token (already set in Railway by the owner).
- `SUPABASE_URL` and `SUPABASE_SECRET_KEY` — existing Supabase connection settings.
- `API_TOKEN` — bearer token for the REST API.
- `CLIENT_WEBHOOK_URL` and `CLIENT_WEBHOOK_SECRET` — optional result callback for the existing client bot.

Operator IDs are configured in `OPERATOR_IDS` at the top of `kaspi_bot.py`. Until IDs are added, submissions are still saved, but operators receive no Telegram notifications and cannot make decisions.

Apply `database/kaspi_receipts.sql` to the existing Supabase project before starting this version. Existing database tables and records are left in place; the new KZT ledger is separate from the former USDT balance.

## Telegram commands

- `/start` — instructions for users.
- `/receipt` — submit a KZT amount and then a photo or document of the receipt.
- `/queue` — operator queue.
- `/balance` — internal confirmed total in KZT.

## API

See [KASPI_API.md](KASPI_API.md) for routes and payloads.

