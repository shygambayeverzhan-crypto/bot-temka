# Telegram Payment Bot

Supabase-backed Telegram bot for manual bank-transfer deposits, receipt review, admin approval, audit logs and 10-minute requisites expiry.

## Railway variables

BOT_TOKEN
SUPABASE_URL
SUPABASE_SECRET_KEY
ADMIN_TELEGRAM_ID (optional)
ADMIN_SETUP_CODE (used by /claim_admin)

Never commit real secrets.

## Start

python bot.py

## Admin

/claim_admin YOUR_SETUP_CODE
/admin
/orders
/wallet_add Name|Bank|Requisites|Holder
/wallet_off UUID
