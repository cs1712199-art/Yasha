
# Yasha — Telegram bookkeeping & tools bot (clone)

This repository contains a runnable clone of the **Yasha** Telegram bot described by the user.  
It implements:
- calculator expressions (with limited % semantics — see below)
- currency conversion (`/rate` or `/EURUSD 100`)- uses exchangerate.host for fiat and Binance public API for crypto
- simple home bookkeeping: accounts, add/delete, record transactions by `/account expression comment`
- show balances (`/give`) and per-account statements
- BTC address recent transaction listing using BlockCypher public API
- archiving ("Yasha, verified")

**Important notes & limitations**:
- The percent operator is implemented only for the common pattern `"<left_expr> [+|-] N%"` where `N%` at the end is interpreted as percentage of the left expression. Example: `(2+3)*100-50%` → `left=(2+3)*100=500` → `500 - 50% of 500 = 250`.
- You must set `TELEGRAM_TOKEN` in environment (see `.env.example`).
- The Google Sheets export is implemented as CSV export only (saved to `/data/exports/*.csv`). You can upload that CSV to Google Sheets manually or automate with your own credentials.
- No persistent database is provided; data is stored in JSON files in `data/` directory. Railway persistent storage is supported (files under `/app/data` in deployed container) but confirm on Railway's docs.

## Files
- `main.py` — bot implementation (python-telegram-bot v20 async)
- `requirements.txt` — required Python packages
- `Procfile` — for Railway (web: python main.py)
- `.env.example` — example environment variables
- `LICENSE` — MIT
- `data/` — created at runtime for storage

## Deploy to Railway (quick)
1. Create a new project on Railway and link your GitHub repo or deploy via the Railway CLI.
2. Make sure to add environment variable `TELEGRAM_TOKEN` with your bot token.
3. Set the start command to `python main.py` (Procfile included).
4. Enable persistent disk if you want JSON data to persist between restarts. Alternatively, connect to an external database and adapt `main.py` accordingly.

## Running locally
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `export TELEGRAM_TOKEN="your_token_here"`
4. `python main.py`

Enjoy!

