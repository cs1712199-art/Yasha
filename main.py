
#!/usr/bin/env python3
"""
Yasha clone - simplified Telegram bot.

Features implemented:
- Calculator with limited % handling
- Currency conversion via exchangerate.host and Binance public API
- Simple bookkeeping with JSON files
- BTC address recent tx listing via BlockCypher
- /add, /delete accounts; /give balances; archival with "Yasha, verified"

Run: set TELEGRAM_TOKEN env var and run `python main.py`
"""

import os
import re
import json
import math
import logging
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from datetime import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
ARCHIVE_FILE = DATA_DIR / "archive.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Please set TELEGRAM_TOKEN env var (see .env.example)")

# Utilities for JSON storage
def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf8"))
    else:
        return default

def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")

# Initialize storage
accounts = load_json(ACCOUNTS_FILE, {})  # { "UAH": { "balance": 0.0, "history": [ {timestamp, amount, comment} ] } }
archive = load_json(ARCHIVE_FILE, {"archived": []})

def format_amount(a, digits=2):
    q = Decimal(a).quantize(Decimal(10) ** -digits, rounding=ROUND_HALF_UP)
    return f"{q:.{digits}f}"

# ---- Calculator / expression evaluation ----
# Limited percent handling: supports patterns like "<left_expr> [+|-] N%"
def eval_expression_with_percent(expr: str):
    expr = expr.strip()
    # detect pattern: "<left> <op> <N>%", where <N>% is at the very end
    m = re.match(r"^(.*?)([+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*%$", expr)
    if m:
        left_expr = m.group(1).strip()
        op = m.group(2)
        n = float(m.group(3))
        left_val = safe_eval(left_expr)
        if left_val is None:
            return None
        percent_val = left_val * (n / 100.0)
        if op == "+":
            return left_val + percent_val
        else:
            return left_val - percent_val
    # fallback: replace X% with (X/100) and eval
    expr2 = re.sub(r"([0-9]+(?:\.[0-9]+)?)\s*%", r"(\1/100)", expr)
    return safe_eval(expr2)

# Very small safe eval for arithmetic expressions only
ALLOWED = re.compile(r"^[0-9\.\+\-\*\/\%\(\)\s]+$")
def safe_eval(expr: str):
    expr = expr.strip()
    # remove trailing percent whitespace issues
    expr = expr.replace("%", "%")
    # allow digits, operators and parentheses only
    if not ALLOWED.match(expr) and "%" not in expr:
        return None
    try:
        # use python eval on a sanitized expression (percent handled by wrapper)
        value = eval(expr, {"__builtins__": None}, {})
        return float(value)
    except Exception as e:
        logger.exception("Eval error: %s", e)
        return None

# ---- Bookkeeping ----
def add_account(name: str, digits: int = 2):
    name = name.upper()
    if name in accounts:
        return False, "Account already exists."
    accounts[name] = {"balance": 0.0, "digits": digits, "history": []}
    save_json(ACCOUNTS_FILE, accounts)
    return True, f"The account was added. The accuracy of {digits} digits after the decimal point is established."

def delete_account(name: str):
    name = name.upper()
    if name not in accounts:
        return False, "Account not found."
    del accounts[name]
    save_json(ACCOUNTS_FILE, accounts)
    return True, "The account was deleted."

def record_transaction(account: str, expr: str, comment: str):
    account = account.upper()
    if account not in accounts:
        # auto-create account with 2 digits
        add_account(account)
    # evaluate expression (support percent handling)
    val = eval_expression_with_percent(expr)
    if val is None:
        return False, "Couldn't evaluate expression."
    # round according to account digits
    digits = accounts[account].get("digits", 2)
    q = float(Decimal(val).quantize(Decimal(10) ** -digits, rounding=ROUND_HALF_UP))
    accounts[account]["balance"] += q
    entry = {"timestamp": datetime.utcnow().isoformat(), "amount": q, "expr": expr, "comment": comment}
    accounts[account]["history"].append(entry)
    save_json(ACCOUNTS_FILE, accounts)
    return True, q

def give_balances():
    lines = []
    for acc, info in accounts.items():
        bal = format_amount(info.get("balance", 0.0), info.get("digits",2))
        lines.append(f"{bal} {acc.lower()}")
    if not lines:
        return "No accounts yet."
    return "Of your funds:\n" + "\n".join(lines)

def give_account_statement(account: str, full=False):
    account = account.upper()
    if account not in accounts:
        return f"Account {account} not found."
    history = accounts[account]["history"]
    if not full:
        # last 20
        history = history[-20:]
    lines = ["Details /" + account, " sum          date  time  comment"]
    for e in reversed(history):
        dt = e.get("timestamp", "")
        try:
            d = datetime.fromisoformat(dt)
            date = d.strftime("%d.%m")
            time = d.strftime("%H:%M")
        except:
            date = dt; time = ""
        amt = format_amount(e["amount"], accounts[account].get("digits",2))
        lines.append(f"{amt:>10} {date} {time} {e.get('comment','')}")
    return "\n".join(lines)

# ---- Currency conversion ----
def rate_query(pair_or_rate: str, amount: float = 1.0):
    pair_or_rate = pair_or_rate.strip().upper()
    # allow formats: EURUSD or /rate eurusd 100
    # try to split into base/quote (3+3 letters)
    m = re.match(r"^([A-Z]{3,4})([_/\\-]?)([A-Z]{3,4})$", pair_or_rate)
    if m:
        base = m.group(1)
        quote = m.group(3)
    else:
        # try to accept spaced pair like "eur usd"
        parts = pair_or_rate.split()
        if len(parts) >= 2:
            base, quote = parts[0].upper(), parts[1].upper()
        else:
            return None, "Couldn't parse currency pair."
    try:
        # handle crypto pairs via Binance for common symbols with USDT/BUSD
        if base in ("BTC","ETH","USDT","USDC") or quote in ("BTC","ETH","USDT","USDC"):
            # try Binance public price for BASEQUOTE e.g. BTCUSDT
            symbol = base + quote
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
            if r.status_code == 200:
                price = float(r.json().get("price", 0.0))
                converted = price * amount
                return (converted, f"{converted} {quote} = ({amount}) {base}", f"1 {base} = {price} {quote}\nat Binance")
            # if not found, try quote+base and invert
            symbol2 = quote + base
            r2 = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol2}")
            if r2.status_code == 200:
                price2 = float(r2.json().get("price", 0.0))
                price = 1.0 / price2 if price2 != 0 else 0
                converted = price * amount
                return (converted, f"{converted} {quote} = ({amount}) {base}", f"1 {base} = {price} {quote}\nat Binance")
        # fallback to exchangerate.host for fiat
        r = requests.get(f"https://api.exchangerate.host/convert?from={base}&to={quote}&amount={amount}")
        if r.status_code == 200:
            j = r.json()
            result = j.get("result")
            rate = j.get("info", {}).get("rate")
            timestamp = j.get("date")
            return (result, f"{result} {quote} = ({amount}) {base}", f"1 {base} = {rate} {quote}\nat {timestamp} exchangerate.host")
    except Exception as e:
        logger.exception("Rate query error: %s", e)
    return None, "Rate lookup failed."

# ---- BTC address tx list via BlockCypher ----
def btc_address_txs(address: str):
    try:
        r = requests.get(f"https://api.blockcypher.com/v1/btc/main/addrs/{address}?limit=50")
        if r.status_code != 200:
            return None, f"Error: {r.status_code}"
        j = r.json()
        txrefs = j.get("txrefs", []) + j.get("unconfirmed_txrefs", [])
        lines = []
        for t in txrefs[:20]:
            # positive if received, negative if spent relative to this address
            value_btc = t.get("value",0) / 1e8
            confirmed = t.get("confirmed")
            timestamp = confirmed or "unconfirmed"
            lines.append({"sum": value_btc, "date": timestamp, "status": "unconfirmed" if not confirmed else "confirmed"})
        return lines, None
    except Exception as e:
        logger.exception("BTC txs error: %s", e)
        return None, str(e)

# ---- Telegram handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yasha clone running. Send commands starting with '/' in groups. Try /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Yasha clone commands:\n"
        "/add <name> [digits] - add account\n"
        "/delete <name> - delete account\n"
        "/give - show balances\n"
        "/give <account> - show account statement\n"
        "To record: /<account> <expr> <comment>  e.g. /UAH 25+5*3-15/5 put in the bedside table\n"
        "/rate <pair> <amount> or /EURUSD 100\n"
        "Send a BTC address (starting with 1 or bc1) to get recent txs.\n"
        "Send an arithmetic expression starting with '/' and I'll evaluate (supports limited %).\n"
        "Say 'Yasha, verified' to archive history (keeps balances).\n"
    )
    await update.message.reply_text(msg)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /add usd [digits]")
        return
    name = args[0]
    digits = int(args[1]) if len(args) > 1 else 2
    ok, msg = add_account(name, digits)
    await update.message.reply_text(msg)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /delete usd")
        return
    ok, msg = delete_account(args[0])
    await update.message.reply_text(msg)

async def give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(give_balances())
        return
    account = args[0]
    # buttons for Current statement / Full statement would be UI; reply with both options textually
    await update.message.reply_text(give_account_statement(account, full=False))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    # check for "Yasha, verified"
    if text.lower() == "yasha, verified":
        # archive history: move all history to archive, keep balance only
        for acc, info in list(accounts.items()):
            if info.get("history"):
                archive["archived"].append({ "account": acc, "history": info["history"], "archived_at": datetime.utcnow().isoformat() })
                accounts[acc]["history"] = []
        save_json(ACCOUNTS_FILE, accounts)
        save_json(ARCHIVE_FILE, archive)
        await update.message.reply_text("Verified. Past movements moved to archive.")
        return

    # if message starts with '/', handle as command-like input for calc or bookkeeping
    if text.startswith("/"):
        body = text[1:].strip()
        # possible commands:
        # 1) /RATEPAIR AMOUNT or /rate eurusd 100
        m_rate = re.match(r"^(rate\s+)?([A-Za-z]{3,4}[_\/\\-]?[A-Za-z]{3,4})\s*([0-9\.\,]*)$", body, re.IGNORECASE)
        if m_rate:
            pair = m_rate.group(2)
            amt = m_rate.group(3) or "1"
            amt = float(amt.replace(",", ".")) if amt else 1.0
            res, info = rate_query(pair, amt)
            if res is None:
                await update.message.reply_text(info)
            else:
                await update.message.reply_text(f"{info}\n{info if isinstance(info,str) else ''}")
            return
        # 2) /add, /delete handled by explicit handlers earlier (those are separate commands)
        # 3) bookkeeping entry: /ACCOUNT EXPR comment...
        m_acc = re.match(r"^([A-Za-z0-9]{1,12})\s+(.+)$", body)
        if m_acc:
            acc = m_acc.group(1)
            rest = m_acc.group(2)
            # split expr and comment: expression is first token group until a letter appears (heuristic)
            # better: assume format "/ACC <expr> <comment>" where expr may contain spaces/ops; we split by first two tokens
            parts = rest.split(maxsplit=1)
            if len(parts) == 1:
                expr = parts[0]
                comment = ""
            else:
                expr, comment = parts[0], parts[1]
            ok, result = record_transaction(acc, expr, comment)
            if not ok:
                await update.message.reply_text(str(result))
            else:
                digits = accounts[acc].get("digits",2)
                await update.message.reply_text(f"Remember. {format_amount(result,digits)}\nBalance: {format_amount(accounts[acc]['balance'], digits)} {acc.lower()}")
            return
        # 4) calculator expression: evaluate the whole body
        val = eval_expression_with_percent(body)
        if val is None:
            await update.message.reply_text("Couldn't evaluate the expression or unrecognized command.")
        else:
            await update.message.reply_text(f"{body} = {format_amount(val, 8)}")
        return

    # if message looks like a BTC address, try to fetch txs
    if re.match(r"^(1|3|bc1)[A-Za-z0-9]{25,}$", text):
        res, err = btc_address_txs(text)
        if err:
            await update.message.reply_text(f"Error fetching BTC address: {err}")
        else:
            if not res:
                await update.message.reply_text("No recent txs found.")
            else:
                lines = ["Details /" + text]
                for t in res[:20]:
                    lines.append(f"{t['sum']:>10} {t['date']} {t['status']}")
                await update.message.reply_text("\n".join(lines))
        return

    # fallback
    await update.message.reply_text("I didn't understand that. Try /help")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("give", give_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/"), message_handler))

    logger.info("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
