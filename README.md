# Airtm Payout Manager Bot

Telegram-first Airtm payout automation foundation.

## Current implementation
- Separate transaction processing by provider operation ID
- Sender -> profile routing
- Versioned profile snapshots
- Exact Decimal money calculations
- Automatic business remainder
- Unknown sender/profile/recipient -> HOLD
- Emergency STOP/RESUME
- Idempotent incoming events
- Transaction and payout ledger
- Dry-run mode (default)
- FastAPI provider webhook endpoint
- Telegram dashboard, held and transaction views
- Audit log for system control changes

## Important
The Airtm provider adapter is intentionally not connected to live money movement. Before production, wire the current Airtm API contract, authentication, callback verification, payout limits and actual fee schedule. Never put API secrets in source control.

## Run
```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

Set `TELEGRAM_BOT_TOKEN` and `ADMIN_TELEGRAM_IDS`. Keep `DRY_RUN=true` while testing.
