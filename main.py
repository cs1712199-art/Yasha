import os
import json
import math
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
DATA_FILE = "data.json"

# ------------------ Storage ------------------
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"accounts": {}, "history": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

data = load_data()

# ------------------ Helpers ------------------
def calc_expression(expr: str) -> float:
    expr = expr.replace("%", "/100")
    return eval(expr, {"__builtins__": None}, {"math": math})

def get_balance(account: str) -> float:
    return round(data["accounts"].get(account, 0.0), 2)

# ------------------ Commands ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! Yasha clone is running on PTB v20+ 🚀")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/add [account] - add account\n"
        "/delete [account] - delete account\n"
        "/give - show balances\n"
        "/[account] [amount expr] [comment] - add record\n"
        "/rate eurusd 100 - currency conversion\n"
    )
    await update.message.reply_text(text)

async def add_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /add usd")
    account = context.args[0].lower()
    data["accounts"][account] = 0.0
    save_data(data)
    await update.message.reply_text(f"✅ Account '{account}' added.")

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /delete usd")
    account = context.args[0].lower()
    if account in data["accounts"]:
        del data["accounts"][account]
        save_data(data)
        await update.message.reply_text(f"🗑 Account '{account}' deleted.")
    else:
        await update.message.reply_text("⚠️ Account not found.")

async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balances = "\n".join([f"{acc.upper()}: {amt:.2f}" for acc, amt in data["accounts"].items()])
    if not balances:
        balances = "No accounts yet."
    await update.message.reply_text(f"📊 Balances:\n{balances}")

async def account_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handles /usd 100 salary
    parts = update.message.text.split(maxsplit=2)
    account = parts[0][1:].lower()
    if account not in data["accounts"]:
        return await update.message.reply_text("⚠️ Account not found. Use /add first.")
    try:
        amount = calc_expression(parts[1])
    except Exception:
        return await update.message.reply_text("⚠️ Invalid math expression.")
    comment = parts[2] if len(parts) > 2 else ""
    data["accounts"][account] += amount
    data["history"].append({"acc": account, "amt": amount, "comment": comment, "time": str(datetime.now())})
    save_data(data)
    await update.message.reply_text(f"💾 Recorded {amount:.2f} {account.upper()} ({comment})")

async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /rate eurusd 100")
    pair = context.args[0].upper()
    amount = float(context.args[1])
    base, quote = pair[:3], pair[3:]
    try:
        resp = requests.get(f"https://api.exchangerate.host/convert?from={base}&to={quote}&amount={amount}").json()
        result = resp["result"]
        rate_val = resp["info"]["rate"]
        await update.message.reply_text(
            f"{amount} {base} = {result:.4f} {quote}\n1 {base} = {rate_val:.4f} {quote}"
        )
    except Exception:
        await update.message.reply_text("⚠️ Conversion failed. Try another pair.")

# ------------------ Main ------------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_account))
    app.add_handler(CommandHandler("delete", delete_account))
    app.add_handler(CommandHandler("give", give))
    app.add_handler(CommandHandler("rate", rate))

    # Catch-all for /usd, /uah, etc.
    app.add_handler(MessageHandler(filters.Regex(r"^/[a-zA-Z]+\s"), account_entry))

    app.run_polling()

if __name__ == "__main__":
    main()
