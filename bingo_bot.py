"""
╔══════════════════════════════════════════════════════════════╗
║           BINGO PRO — TELEGRAM PAYMENT BOT                   ║
║  Features:                                                    ║
║  ✅ Payment screenshot notification + Admin approve/reject    ║
║  ✅ /balance — User balance check                             ║
║  ✅ Withdrawal request system                                 ║
║  ✅ Anti-fraud duplicate screenshot detection                 ║
║  ✅ Admin daily summary report                                ║
║  ✅ Flask keep-alive (Render.com)                             ║
╚══════════════════════════════════════════════════════════════╝

📦 Install:
    pip install pyTelegramBotAPI firebase-admin flask

▶ Run:
    python bingo_bot.py
"""

import os
import json
import hashlib
import threading
from datetime import datetime, timedelta

import firebase_admin
from firebase_admin import credentials, db as firebase_db

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)

from flask import Flask

# ══════════════════════════════════════════════
#  ⚙️  CONFIG
# ══════════════════════════════════════════════
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
ADMIN_ID   = 6883208728
WEBAPP_URL = "https://bingo-game-4.onrender.com"

FIREBASE_DB_URL = "https://house-rent-app-3674a-default-rtdb.firebaseio.com/"

MIN_WITHDRAWAL = 50
MAX_WITHDRAWAL = 5000

DAILY_REPORT_HOUR   = 20
DAILY_REPORT_MINUTE = 0

# ══════════════════════════════════════════════
#  🌐 FLASK KEEP-ALIVE (Render.com)
# ══════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bingo Bot is running ✅"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ══════════════════════════════════════════════
#  🔥 FIREBASE INIT
# ══════════════════════════════════════════════
_firebase_key = os.environ.get("FIREBASE_KEY", "")
if _firebase_key:
    cred = credentials.Certificate(json.loads(_firebase_key))
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

def fb_get(path):
    return firebase_db.reference(path).get()

def fb_set(path, value):
    firebase_db.reference(path).set(value)

def fb_push(path, value):
    return firebase_db.reference(path).push(value)

# ══════════════════════════════════════════════
#  🔒 ANTI-FRAUD
# ══════════════════════════════════════════════
def compute_hash(file_id: str) -> str:
    return hashlib.sha256(file_id.encode()).hexdigest()

def is_duplicate(file_id: str) -> bool:
    h    = compute_hash(file_id)
    used = fb_get("bot/used_hashes") or {}
    return h in used

def save_hash(file_id: str, user_id: str, amount: int):
    h = compute_hash(file_id)
    fb_set(f"bot/used_hashes/{h}", {
        "user_id": user_id,
        "amount":  amount,
        "time":    datetime.now().isoformat()
    })

def has_pending(user_id: str) -> bool:
    payments = fb_get("payments") or {}
    for p in payments.values():
        if str(p.get("user_id")) == str(user_id) and p.get("status") == "pending":
            return True
    return False

# ══════════════════════════════════════════════
#  🤖 BOT INIT
# ══════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ══════════════════════════════════════════════
#  📋 MENU
# ══════════════════════════════════════════════
def send_menu(chat_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(
        "🎮 Play Game",
        web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={chat_id}")
    ))
    kb.add(
        InlineKeyboardButton("💳 Deposit",  callback_data="deposit"),
        InlineKeyboardButton("💰 Balance",  callback_data="balance")
    )
    kb.add(
        InlineKeyboardButton("🏧 Withdraw", callback_data="withdraw"),
        InlineKeyboardButton("📊 History",  callback_data="history")
    )
    bot.send_message(chat_id, "🎮 <b>Bingo Pro</b> — Menu 👇", reply_markup=kb)

# ══════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = str(m.chat.id)
    if not fb_get(f"users/{uid}/balance"):
        fb_set(f"users/{uid}/balance", 0)
        fb_set(f"users/{uid}/username", m.from_user.username or m.from_user.first_name)
    send_menu(m.chat.id)

@bot.message_handler(commands=["balance"])
def cmd_balance(m):
    uid        = str(m.chat.id)
    bal        = fb_get(f"users/{uid}/balance") or 0
    pending_wd = fb_get(f"users/{uid}/pending_withdrawal") or 0
    text = f"💰 <b>Balance: {bal} ብር</b>"
    if pending_wd:
        text += f"\n⏳ Pending Withdrawal: {pending_wd} ብር"
    bot.send_message(m.chat.id, text)

# ══════════════════════════════════════════════
#  📸 PHOTO HANDLER
# ══════════════════════════════════════════════
@bot.message_handler(content_types=["photo", "document"])
def handle_proof(m):
    uid  = str(m.from_user.id)
    temp = fb_get(f"temp/{uid}")

    if not temp:
        bot.send_message(m.chat.id, "❗ መጀመሪያ <b>Deposit</b> ምረጥ")
        return

    amount  = temp["amount"]
    file_id = m.photo[-1].file_id if m.content_type == "photo" else m.document.file_id

    if is_duplicate(file_id):
        bot.send_message(m.chat.id,
            "🚫 <b>ይህ Screenshot አስቀድሞ ጥቅም ላይ ዋሎ!</b>\n"
            "Duplicate payment ተፈቅዶ አይሰጥም።")
        fb_set(f"temp/{uid}", None)
        return

    if has_pending(uid):
        bot.send_message(m.chat.id,
            "⚠️ <b>አስቀድሞ Pending Payment አለዎት!</b>\nAdmin ያረጋግጣል — ጠብቁ።")
        return

    save_hash(file_id, uid, amount)

    pid = fb_push("payments", {
        "user_id":  uid,
        "username": m.from_user.username or m.from_user.first_name,
        "amount":   amount,
        "file_id":  file_id,
        "status":   "pending",
        "time":     int(datetime.now().timestamp() * 1000)
    }).key

    fb_set(f"temp/{uid}", None)

    bot.send_message(m.chat.id,
        f"⏳ <b>{amount} ብር</b> — Admin ያረጋግጣል...")

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Approve", callback_data=f"ap_{pid}_{uid}_{amount}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"re_{pid}_{uid}")
    )
    name = m.from_user.username or m.from_user.first_name
    try:
        bot.send_photo(ADMIN_ID, file_id,
            caption=f"💳 <b>New Deposit</b>\n👤 {name} (<code>{uid}</code>)\n💰 {amount} ብር",
            reply_markup=kb)
    except Exception as e:
        print(f"Admin notify error: {e}")

# ══════════════════════════════════════════════
#  📝 TEXT HANDLER — Withdrawal flow
# ══════════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    uid  = str(m.from_user.id)
    text = m.text.strip()
    state = fb_get(f"bot/state/{uid}")

    # Withdrawal amount
    if state == "waiting_wd_amount":
        try:
            amount  = int(text)
            balance = fb_get(f"users/{uid}/balance") or 0
            if amount < MIN_WITHDRAWAL:
                bot.send_message(m.chat.id, f"❌ Minimum: <b>{MIN_WITHDRAWAL} ብር</b>"); return
            if amount > MAX_WITHDRAWAL:
                bot.send_message(m.chat.id, f"❌ Maximum: <b>{MAX_WITHDRAWAL} ብར</b>"); return
            if amount > balance:
                bot.send_message(m.chat.id, f"❌ Balance አናሳ! (Balance: {balance} ብር)"); return

            fb_set(f"bot/state/{uid}", "waiting_wd_account")
            fb_set(f"temp_wd/{uid}/amount", amount)

            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🏦 CBE",      callback_data="wdm_CBE"),
                InlineKeyboardButton("📱 Telebirr", callback_data="wdm_Telebirr"),
                InlineKeyboardButton("🏧 Awash",    callback_data="wdm_Awash"),
                InlineKeyboardButton("💳 Other",    callback_data="wdm_Other"),
            )
            bot.send_message(m.chat.id,
                f"🏧 <b>{amount} ብር</b> — ምን አይነት account?", reply_markup=kb)
        except ValueError:
            bot.send_message(m.chat.id, "❌ ቁጥር ብቻ ላክ! ለምሳሌ: <code>500</code>")
        return

    # Withdrawal account number
    if state == "waiting_wd_acct_num":
        account = text
        amount  = fb_get(f"temp_wd/{uid}/amount") or 0
        method  = fb_get(f"temp_wd/{uid}/method") or "—"
        balance = fb_get(f"users/{uid}/balance") or 0

        fb_set(f"users/{uid}/balance", balance - amount)
        fb_set(f"users/{uid}/pending_withdrawal", amount)

        wid = fb_push("bot/withdrawals", {
            "user_id": uid,
            "amount":  amount,
            "method":  method,
            "account": account,
            "status":  "pending",
            "time":    datetime.now().strftime("%Y-%m-%d %H:%M")
        }).key

        fb_set(f"bot/state/{uid}", None)
        fb_set(f"temp_wd/{uid}", None)

        bot.send_message(m.chat.id,
            f"✅ <b>Withdrawal Request ተልኳል!</b>\n\n"
            f"💰 {amount} ብር\n📲 {method} — <code>{account}</code>\n\n"
            f"⏳ Admin ያስተናግዳቸዋል")

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ Paid",   callback_data=f"wda_{wid}_{uid}_{amount}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"wdr_{wid}_{uid}_{amount}")
        )
        name = m.from_user.username or m.from_user.first_name
        bot.send_message(ADMIN_ID,
            f"🏧 <b>New Withdrawal</b>\n👤 {name} (<code>{uid}</code>)\n"
            f"💰 {amount} ብር\n📲 {method} — <code>{account}</code>",
            reply_markup=kb)
        return

# ══════════════════════════════════════════════
#  🔘 CALLBACK ROUTER
# ══════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    bot.answer_callback_query(c.id)
    uid  = str(c.from_user.id)
    data = c.data

    # Deposit menu
    if data == "deposit":
        kb = InlineKeyboardMarkup(row_width=1)
        for a in [50, 100, 200, 500, 1000]:
            kb.add(InlineKeyboardButton(f"{a} ብር", callback_data=f"pay_{a}"))
        bot.send_message(c.message.chat.id, "💳 <b>Amount ምረጥ:</b>", reply_markup=kb)

    elif data.startswith("pay_"):
        amount = int(data.split("_")[1])
        fb_set(f"temp/{uid}", {"amount": amount})
        bot.send_message(c.message.chat.id,
            f"✅ <b>{amount} ብር</b> selected\n\n"
            f"🏦 CBE: <code>1000641057146</code>\n\n"
            f"📸 Screenshot ላክ")

    elif data == "balance":
        bal        = fb_get(f"users/{uid}/balance") or 0
        pending_wd = fb_get(f"users/{uid}/pending_withdrawal") or 0
        text = f"💰 <b>Balance: {bal} ብር</b>"
        if pending_wd:
            text += f"\n⏳ Pending Withdrawal: {pending_wd} ብር"
        bot.send_message(c.message.chat.id, text)

    elif data == "withdraw":
        bal = fb_get(f"users/{uid}/balance") or 0
        if bal < MIN_WITHDRAWAL:
            bot.send_message(c.message.chat.id,
                f"❌ Balance አናሳ!\nMinimum: <b>{MIN_WITHDRAWAL} ብር</b>\nአሁን: <b>{bal} ብር</b>")
            return
        fb_set(f"bot/state/{uid}", "waiting_wd_amount")
        bot.send_message(c.message.chat.id,
            f"🏧 <b>Withdrawal</b>\n💰 Balance: <b>{bal} ብር</b>\n\nምን ያህል? ቁጥር ላክ:")

    elif data == "history":
        payments  = fb_get("payments") or {}
        user_txns = [p for p in payments.values() if str(p.get("user_id")) == uid]
        if not user_txns:
            bot.send_message(c.message.chat.id, "📊 ምንም ታሪክ የለም"); return
        user_txns.sort(key=lambda x: x.get("time", 0), reverse=True)
        icons = {"approved": "✅", "rejected": "❌", "pending": "⏳"}
        lines = ["📊 <b>ግብይት ታሪክ:</b>\n"]
        for p in user_txns[:10]:
            icon = icons.get(p.get("status"), "❓")
            t    = datetime.fromtimestamp(p.get("time", 0)/1000).strftime("%m/%d %H:%M") if p.get("time") else "—"
            lines.append(f"{icon} {p.get('amount',0)} ብር — {t}")
        bot.send_message(c.message.chat.id, "\n".join(lines))

    # Withdrawal method
    elif data.startswith("wdm_"):
        method = data.replace("wdm_", "")
        fb_set(f"temp_wd/{uid}/method", method)
        fb_set(f"bot/state/{uid}", "waiting_wd_acct_num")
        bot.send_message(c.message.chat.id,
            f"📲 <b>{method}</b>\n\n🔢 Account number ላክ:")

    # ── Admin: Approve deposit ──
    elif data.startswith("ap_"):
        parts  = data.split("_")
        pid    = parts[1]
        u_id   = parts[2]
        amount = int(parts[3])
        bal    = fb_get(f"users/{u_id}/balance") or 0
        fb_set(f"users/{u_id}/balance", bal + amount)
        fb_set(f"payments/{pid}/status", "approved")
        try:
            bot.edit_message_caption(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n✅ <b>APPROVED</b>")
        except Exception: pass
        bot.send_message(u_id,
            f"✅ <b>{amount} ብር</b> ታከለ!\nNew Balance: <b>{bal+amount} ብር</b>")

    # ── Admin: Reject deposit ──
    elif data.startswith("re_"):
        parts = data.split("_")
        pid   = parts[1]
        u_id  = parts[2]
        fb_set(f"payments/{pid}/status", "rejected")
        try:
            bot.edit_message_caption(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n❌ <b>REJECTED</b>")
        except Exception: pass
        bot.send_message(u_id, "❌ <b>Deposit Rejected</b>\nAdmin ያናግሩ።")

    # ── Admin: Approve withdrawal ──
    elif data.startswith("wda_"):
        parts  = data.split("_")
        wid    = parts[1]
        u_id   = parts[2]
        amount = int(parts[3])
        fb_set(f"bot/withdrawals/{wid}/status", "approved")
        fb_set(f"users/{u_id}/pending_withdrawal", 0)
        try:
            bot.edit_message_text(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                text=c.message.text + "\n\n✅ <b>PAID</b>")
        except Exception: pass
        bot.send_message(u_id, f"✅ <b>{amount} ብር</b> ተላከ!")

    # ── Admin: Reject withdrawal ──
    elif data.startswith("wdr_"):
        parts  = data.split("_")
        wid    = parts[1]
        u_id   = parts[2]
        amount = int(parts[3])
        fb_set(f"bot/withdrawals/{wid}/status", "rejected")
        bal = fb_get(f"users/{u_id}/balance") or 0
        fb_set(f"users/{u_id}/balance", bal + amount)
        fb_set(f"users/{u_id}/pending_withdrawal", 0)
        try:
            bot.edit_message_text(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                text=c.message.text + "\n\n❌ <b>REJECTED — Refunded</b>")
        except Exception: pass
        bot.send_message(u_id,
            f"❌ Withdrawal Rejected\n💰 <b>{amount} ብር</b> balance ላይ ተመለሰ!")

# ══════════════════════════════════════════════
#  📊 DAILY REPORT — Background thread
# ══════════════════════════════════════════════
def daily_report_loop():
    import time
    while True:
        now      = datetime.now()
        next_run = now.replace(hour=DAILY_REPORT_HOUR, minute=DAILY_REPORT_MINUTE, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        time.sleep((next_run - now).total_seconds())
        try:
            payments    = fb_get("payments") or {}
            withdrawals = fb_get("bot/withdrawals") or {}
            users       = fb_get("users") or {}
            today       = datetime.now().strftime("%Y-%m-%d")
            today_ts    = datetime.now().replace(hour=0,minute=0,second=0).timestamp() * 1000

            dep_today = [p for p in payments.values()
                         if p.get("time",0) >= today_ts and p.get("status") == "approved"]
            wd_today  = [w for w in withdrawals.values()
                         if w.get("status") == "approved" and today in str(w.get("time",""))]
            total_dep = sum(p.get("amount",0) for p in dep_today)
            total_wd  = sum(w.get("amount",0) for w in wd_today)
            pend_dep  = sum(1 for p in payments.values()    if p.get("status") == "pending")
            pend_wd   = sum(1 for w in withdrawals.values() if w.get("status") == "pending")
            total_bal = sum((u.get("balance") or 0) for u in users.values())

            bot.send_message(ADMIN_ID,
                f"📊 <b>Daily Report — {today}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💳 Deposits: <b>{len(dep_today)}</b> ({total_dep} ብር)\n"
                f"🏧 Withdrawals: <b>{len(wd_today)}</b> ({total_wd} ብር)\n\n"
                f"⏳ Pending Deposits: {pend_dep}\n"
                f"⏳ Pending Withdrawals: {pend_wd}\n\n"
                f"👥 Users: {len(users)}\n"
                f"💰 Total Balance: {total_bal} ብር\n"
                f"📈 Net: {total_dep - total_wd} ብር")
        except Exception as e:
            print(f"Daily report error: {e}")

threading.Thread(target=daily_report_loop, daemon=True).start()

# ══════════════════════════════════════════════
#  🚀 RUN
# ══════════════════════════════════════════════
print("🤖 Bingo Bot running...")
bot.infinity_polling(skip_pending=True)
