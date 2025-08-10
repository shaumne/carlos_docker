Overview

This system generates trading signals (yf.py) and executes orders while maintaining Google Sheets state (trade_executor.py). It has been refactored to production-grade with:
- SQLite-backed durable queue and active positions persistence
- Google Sheets batchUpdate with exponential backoff and idempotency
- TP/SL atomic cancellation and reconciliation
- Health endpoint and tests

Requirements
- Python 3.10+
- Environment variables in .env
- Google service account JSON (GOOGLE_CREDENTIALS_FILE)

Key env vars
- GOOGLE_SHEET_ID
- GOOGLE_CREDENTIALS_FILE (default: credentials.json)
- GOOGLE_WORKSHEET_NAME (default: Trading)
- ARCHIVE_WORKSHEET_NAME (default: Archive)
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (optional alerts)
- TRADE_AMOUNT (default 10)
- TRADE_CHECK_INTERVAL, BATCH_SIZE
- HEALTH_PORT (default 8080)

Setup
1) Create and activate venv.
2) pip install -r requirements.txt
3) Put credentials.json in project root or set full path in env.
4) Export env vars or use .env.

Run
- Signals + Sheets updater: python yf.py
- Executor: python trade_executor.py
- Health: GET http://localhost:8080/health

Migration
- Legacy Excel queue -> SQLite:
  python scripts/migrate_pending_to_db.py

Tests
- pytest -q

Notes
- All code, identifiers, and sheet headers remain in English; runtime logs may include Turkish notes.

