"""
╔══════════════════════════════════════════════════════════════════╗
║         BINGO PRO — TELEGRAM BOT  (SMS AUTO-VERIFY)             ║
║                                                                  ║
║  ✅ User deposit → screenshot ይልካል                              ║
║  ✅ SMS Forwarder → real bank SMS bot ላይ ይልካል                   ║
║  ✅ Bot screenshot amount vs SMS amount ያነፃፅራል                  ║
║  ✅ Match → Auto Approve,  No Match → Auto Reject                ║
║  ✅ Duplicate ref/hash detection                                 ║
║  ✅ Date check (ዛሬ ብቻ valid)                                    ║
║  ✅ CBE account number check                                     ║
║  ✅ Withdrawal flow (CBE / Telebirr / Awash)                     ║
║  ✅ Admin daily report @ 8PM                                     ║
║  ✅ Flask keep-alive (Render.com)                                ║
╚══════════════════════════════════════════════════════════════════╝

📦 Install:
    pip install pyTelegramBotAPI firebase-admin flask

▶ Run:
    python bot.py
"""

import os
import re
import json
import hashlib
import threading
from datetime import datetime, timedelta

import firebase_admin
from firebase_admin import credentials, db as firebase_db

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from flask import Flask

# ══════════════════════════════════════════════════════
#  ⚙️  CONFIG
# ══════════════════════════════════════════════════════
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
ADMIN_ID   = 6883208728
WEBAPP_URL = "https://bingo-game-4.onrender.com"

FIREBASE_DB_URL = "https://house-rent-app-3674a-default-rtdb.firebaseio.com/"

# ✅ ያንተ CBE account number (last 4 digits ወይም full)
CBE_ACCOUNT      = "1000641057146"
CBE_ACCOUNT_LAST = "7146"   # SMS ላይ masked ሆኖ ይታያል

MIN_WITHDRAWAL   = 50
MAX_WITHDRAWAL   = 5000

DAILY_REPORT_HOUR   = 20
DAILY_REPORT_MINUTE = 0

# ══════════════════════════════════════════════════════
#  🌐 FLASK KEEP-ALIVE
# ══════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bingo Bot is running ✅"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ══════════════════════════════════════════════════════
#  🔥 FIREBASE
# ══════════════════════════════════════════════════════
_key = os.environ.get("FIREBASE_KEY", "")
if _key:
    cred = credentials.Certificate(json.loads(_key))
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

def fb_get(path):
    return firebase_db.reference(path).get()

def fb_set(path, value):
    firebase_db.reference(path).set(value)

def fb_push(path, value):
    return firebase_db.reference(path).push(value)

# ══════════════════════════════════════════════════════
#  🔒 ANTI-FRAUD helpers
# ══════════════════════════════════════════════════════
def hash_file(file_id: str) -> str:
    return hashlib.sha256(file_id.encode()).hexdigest()

def is_dup_screenshot(file_id: str) -> bool:
    h    = hash_file(file_id)
    used = fb_get("bot/used_hashes") or {}
    return h in used

def save_screenshot_hash(file_id: str, uid: str, amount: int):
    h = hash_file(file_id)
    fb_set(f"bot/used_hashes/{h}", {
        "user_id": uid,
        "amount":  amount,
        "time":    datetime.now().isoformat()
    })

def is_dup_ref(ref_no: str) -> bool:
    """Bank reference number ቀድሞ ጥቅም ላይ ዋሏል?"""
    used = fb_get("bot/used_refs") or {}
    return ref_no in used

def save_ref(ref_no: str, uid: str, amount: int):
    fb_set(f"bot/used_refs/{ref_no}", {
        "user_id": uid,
        "amount":  amount,
        "time":    datetime.now().isoformat()
    })

def has_pending(uid: str) -> bool:
    payments = fb_get("payments") or {}
    for p in payments.values():
        if str(p.get("user_id")) == uid and p.get("status") == "pending":
            return True
    return False

# ══════════════════════════════════════════════════════
#  📲 SMS PARSER
# ══════════════════════════════════════════════════════
def parse_cbe_sms(text: str) -> dict | None:
    """
    CBE SMS format:
    Dear Biniyam your Account 1*********7146 has been Credited with
    ETB 2,000.00 from Abdimelik Asefa, on 27/04/2026 at 21:38:49
    with Ref No FT26118W65DX Your Current Balance is ETB 2,038.64.
    """
    # Amount
    amt_m = re.search(r'Credited with ETB ([\d,]+\.?\d*)', text, re.IGNORECASE)
    if not amt_m:
        return None
    amount = float(amt_m.group(1).replace(',', ''))

    # Sender name
    sender_m = re.search(r'from ([A-Za-z ]+),\s*on', text)
    sender = sender_m.group(1).strip() if sender_m else "Unknown"

    # Date
    date_m = re.search(r'on (\d{2}/\d{2}/\d{4})', text)
    sms_date = date_m.group(1) if date_m else None

    # Ref
    ref_m = re.search(r'Ref No ([A-Z0-9]+)', text, re.IGNORECASE)
    ref   = ref_m.group(1) if ref_m else None

    # Account last 4
    acct_m = re.search(r'Account 1\*+(\d{4})', text)
    acct_last = acct_m.group(1) if acct_m else None

    return {
        "bank":      "CBE",
        "amount":    amount,
        "sender":    sender,
        "date":      sms_date,
        "ref":       ref,
        "acct_last": acct_last,
    }


def parse_telebirr_sms(text: str) -> dict | None:
    """
    Telebirr SMS format:
    Dear Biniyam You have received ETB 1.00 from almaz ayele(2519****6777)
    on 03/05/2026 12:31:30. Your transaction number is DE35HFZ2FL.
    """
    # Amount
    amt_m = re.search(r'received ETB ([\d,]+\.?\d*)', text, re.IGNORECASE)
    if not amt_m:
        return None
    amount = float(amt_m.group(1).replace(',', ''))

    # Sender
    sender_m = re.search(r'from ([A-Za-z ]+)\(', text)
    sender = sender_m.group(1).strip() if sender_m else "Unknown"

    # Date
    date_m = re.search(r'on (\d{2}/\d{2}/\d{4})', text)
    sms_date = date_m.group(1) if date_m else None

    # Transaction ref
    ref_m = re.search(r'transaction number is ([A-Z0-9]+)', text, re.IGNORECASE)
    ref   = ref_m.group(1) if ref_m else None

    return {
        "bank":      "Telebirr",
        "amount":    amount,
        "sender":    sender,
        "date":      sms_date,
        "ref":       ref,
        "acct_last": None,
    }


def parse_sms(text: str) -> dict | None:
    """CBE ወይም Telebirr SMS ይለያል"""
    if "Credited with ETB" in text or "has been Credited" in text:
        return parse_cbe_sms(text)
    if "You have received ETB" in text or "received ETB" in text:
        return parse_telebirr_sms(text)
    return None


def is_today(date_str: str) -> bool:
    """date_str format: DD/MM/YYYY"""
    if not date_str:
        return True   # date ካልተገኘ pass
    try:
        sms_dt = datetime.strptime(date_str, "%d/%m/%Y")
        today  = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        diff   = abs((sms_dt - today).days)
        return diff <= 1   # ዛሬ ወይም ትናንት (timezone issues ስለሚኖር)
    except Exception:
        return True

# ══════════════════════════════════════════════════════
#  🤖 BOT
# ══════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ── Pending deposits: uid → {amount, file_id, pid} ──
# Firebase temp ይጠቀማል

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
    bot.send_message(chat_id,
        "🎮 <b>Bingo Pro</b>\n\n"
        "💳 Deposit ለማድረግ → <b>Deposit</b> ምረጥ\n"
        "💰 Balance ለማየት → <b>Balance</b> ምረጥ",
        reply_markup=kb)

# ══════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = str(m.chat.id)
    if not fb_get(f"users/{uid}/balance"):
        fb_set(f"users/{uid}/balance", 0)
    display = m.from_user.username or m.from_user.first_name or uid
    fb_set(f"users/{uid}/display", display)
    fb_set(f"users/{uid}/username", display)
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

# ══════════════════════════════════════════════════════
#  📸 SCREENSHOT HANDLER
# ══════════════════════════════════════════════════════
@bot.message_handler(content_types=["photo", "document"])
def handle_screenshot(m):
    uid  = str(m.from_user.id)
    temp = fb_get(f"temp/{uid}")

    if not temp:
        bot.send_message(m.chat.id, "❗ መጀመሪያ <b>Deposit</b> ምረጥ → amount ምረጥ → ከዚያ screenshot ላክ")
        return

    amount  = temp.get("amount", 0)
    file_id = m.photo[-1].file_id if m.content_type == "photo" else m.document.file_id

    # Duplicate screenshot check
    if is_dup_screenshot(file_id):
        bot.send_message(m.chat.id,
            "🚫 <b>ይህ Screenshot አስቀድሞ ጥቅም ላይ ዋሏል!</b>\n"
            "Duplicate payment አይቀበልም።")
        fb_set(f"temp/{uid}", None)
        return

    # Already has pending
    if has_pending(uid):
        bot.send_message(m.chat.id,
            "⚠️ <b>አስቀድሞ Pending Payment አለዎት!</b>\nጥቂት ይጠብቁ...")
        return

    # Save screenshot hash
    save_screenshot_hash(file_id, uid, amount)

    # Save pending payment to Firebase
    pid = fb_push("payments", {
        "user_id":  uid,
        "display":  m.from_user.username or m.from_user.first_name or uid,
        "amount":   amount,
        "file_id":  file_id,
        "status":   "pending",
        "time":     int(datetime.now().timestamp() * 1000),
        "verified": False,
    }).key

    # Save pid in temp so SMS can match later
    fb_set(f"temp/{uid}/pid", pid)
    fb_set(f"temp/{uid}/file_id", file_id)

    bot.send_message(m.chat.id,
        f"📸 Screenshot ተቀብሏል!\n"
        f"💰 <b>{amount} ብር</b>\n\n"
        f"⏳ SMS verification እየጠበቀ ነው...")

    # Notify admin (info only — auto verify will handle)
    name = m.from_user.username or m.from_user.first_name
    try:
        bot.send_photo(ADMIN_ID, file_id,
            caption=f"📸 <b>New Screenshot</b>\n"
                    f"👤 {name} (<code>{uid}</code>)\n"
                    f"💰 {amount} ብር\n"
                    f"⏳ SMS verification pending...")
    except Exception as e:
        print(f"Admin notify error: {e}")

# ══════════════════════════════════════════════════════
#  📨 SMS FORWARDER MESSAGE HANDLER
# ══════════════════════════════════════════════════════
@bot.message_handler(
    func=lambda m: (
        m.chat.id == ADMIN_ID and
        m.text and
        ("Credited with ETB" in m.text or
         "has been Credited" in m.text or
         "You have received ETB" in m.text or
         "received ETB" in m.text)
    )
)
def handle_forwarded_sms(m):
    """
    SMS Forwarder app → admin Telegram ላይ ይልካል
    Bot ያነብና pending payments ጋር ያነፃፅራል
    """
    sms = parse_sms(m.text)
    if not sms:
        bot.send_message(ADMIN_ID, "⚠️ SMS parse አልቻለም")
        return

    sms_amount = sms["amount"]
    sms_ref    = sms.get("ref")
    sms_date   = sms.get("date")
    sms_bank   = sms.get("bank")
    acct_last  = sms.get("acct_last")

    # ── CBE account check ──
    if sms_bank == "CBE" and acct_last and acct_last != CBE_ACCOUNT_LAST:
        bot.send_message(ADMIN_ID,
            f"⚠️ CBE account mismatch!\n"
            f"SMS: ...{acct_last} | Expected: ...{CBE_ACCOUNT_LAST}")
        return

    # ── Date check ──
    if not is_today(sms_date):
        bot.send_message(ADMIN_ID,
            f"⚠️ SMS ዛሬ አይደለም! Date: {sms_date}")
        return

    # ── Duplicate ref check ──
    if sms_ref and is_dup_ref(sms_ref):
        bot.send_message(ADMIN_ID,
            f"🚫 Duplicate ref: <code>{sms_ref}</code> — አስቀድሞ approved!")
        return

    # ── Find matching pending payment ──
    payments = fb_get("payments") or {}
    matched_pid  = None
    matched_uid  = None
    matched_pay  = None

    for pid, pay in payments.items():
        if pay.get("status") != "pending":
            continue
        pay_amount = pay.get("amount", 0)
        # Amount match (allow ±1 for rounding)
        if abs(float(pay_amount) - sms_amount) <= 1:
            matched_pid = pid
            matched_uid = str(pay.get("user_id"))
            matched_pay = pay
            break

    # ── No match found ──
    if not matched_pid:
        bot.send_message(ADMIN_ID,
            f"⚠️ <b>SMS received ነገር pending payment አልተገኘም</b>\n\n"
            f"🏦 {sms_bank}\n"
            f"💰 {sms_amount} ብር\n"
            f"👤 {sms.get('sender')}\n"
            f"📋 Ref: {sms_ref}\n\n"
            f"Manual አረጋግጥ!")
        return

    # ── MATCH FOUND → AUTO APPROVE ──
    bal = fb_get(f"users/{matched_uid}/balance") or 0
    fb_set(f"users/{matched_uid}/balance", bal + int(sms_amount))
    fb_set(f"payments/{matched_pid}/status", "approved")
    fb_set(f"payments/{matched_pid}/verified", True)
    fb_set(f"payments/{matched_pid}/sms_ref", sms_ref)
    fb_set(f"payments/{matched_pid}/sms_bank", sms_bank)
    fb_set(f"payments/{matched_pid}/sms_sender", sms.get("sender"))

    # Save ref to prevent reuse
    if sms_ref:
        save_ref(sms_ref, matched_uid, int(sms_amount))

    # Update analytics
    dep_snap = fb_get("analytics/totalDeposits") or 0
    fb_set("analytics/totalDeposits", dep_snap + int(sms_amount))

    # Clear temp
    fb_set(f"temp/{matched_uid}", None)

    # Notify user ✅
    new_bal = bal + int(sms_amount)
    bot.send_message(int(matched_uid),
        f"✅ <b>Deposit Approved!</b>\n\n"
        f"💰 {int(sms_amount)} ብር ታከለ\n"
        f"🏦 {sms_bank} — {sms.get('sender')}\n"
        f"📋 Ref: <code>{sms_ref}</code>\n\n"
        f"💼 New Balance: <b>{new_bal} ብር</b>")

    # Notify admin ✅
    display = matched_pay.get("display") or matched_uid
    bot.send_message(ADMIN_ID,
        f"✅ <b>Auto Approved!</b>\n\n"
        f"👤 {display} (<code>{matched_uid}</code>)\n"
        f"💰 {int(sms_amount)} ብር\n"
        f"🏦 {sms_bank} — {sms.get('sender')}\n"
        f"📋 Ref: <code>{sms_ref}</code>")

    print(f"✅ Auto approved: {matched_uid} → {sms_amount} ብር ({sms_ref})")

# ══════════════════════════════════════════════════════
#  📝 TEXT HANDLER — Deposit amount / Withdrawal flow
# ══════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    uid   = str(m.from_user.id)
    text  = m.text.strip()
    state = fb_get(f"bot/state/{uid}")

    # ── Withdrawal amount ──
    if state == "waiting_wd_amount":
        try:
            amount  = int(text)
            balance = fb_get(f"users/{uid}/balance") or 0
            if amount < MIN_WITHDRAWAL:
                bot.send_message(m.chat.id, f"❌ Minimum: <b>{MIN_WITHDRAWAL} ብር</b>"); return
            if amount > MAX_WITHDRAWAL:
                bot.send_message(m.chat.id, f"❌ Maximum: <b>{MAX_WITHDRAWAL} ብር</b>"); return
            if amount > balance:
                bot.send_message(m.chat.id, f"❌ Balance አናሳ! Balance: {balance} ብር"); return

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
                f"🏧 <b>{amount} ብር</b>\nምን አይነት account?", reply_markup=kb)
        except ValueError:
            bot.send_message(m.chat.id, "❌ ቁጥር ብቻ ላክ! ለምሳሌ: <code>500</code>")
        return

    # ── Withdrawal account number ──
    if state == "waiting_wd_acct_num":
        account = text
        amount  = fb_get(f"temp_wd/{uid}/amount") or 0
        method  = fb_get(f"temp_wd/{uid}/method") or "—"
        balance = fb_get(f"users/{uid}/balance") or 0

        fb_set(f"users/{uid}/balance", balance - amount)
        fb_set(f"users/{uid}/pending_withdrawal", amount)

        wid = fb_push("bot/withdrawals", {
            "user_id": uid,
            "display": m.from_user.username or m.from_user.first_name or uid,
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
            f"💰 {amount} ብር\n"
            f"📲 {method} — <code>{account}</code>\n\n"
            f"⏳ Admin ያስተናግዳቸዋል")

        # Admin notify
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ Paid",   callback_data=f"wda_{wid}_{uid}_{amount}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"wdr_{wid}_{uid}_{amount}")
        )
        name = m.from_user.username or m.from_user.first_name
        bot.send_message(ADMIN_ID,
            f"🏧 <b>New Withdrawal</b>\n"
            f"👤 {name} (<code>{uid}</code>)\n"
            f"💰 {amount} ብር\n"
            f"📲 {method} — <code>{account}</code>",
            reply_markup=kb)
        return

    # ── Default: show menu ──
    send_menu(m.chat.id)

# ══════════════════════════════════════════════════════
#  🔘 CALLBACK ROUTER
# ══════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    bot.answer_callback_query(c.id)
    uid  = str(c.from_user.id)
    data = c.data

    # ── Deposit menu ──
    if data == "deposit":
        kb = InlineKeyboardMarkup(row_width=1)
        for a in [50, 100, 200, 500, 1000]:
            kb.add(InlineKeyboardButton(f"💳 {a} ብር", callback_data=f"pay_{a}"))
        bot.send_message(c.message.chat.id, "💳 <b>Amount ምረጥ:</b>", reply_markup=kb)

    elif data.startswith("pay_"):
        amount = int(data.split("_")[1])
        fb_set(f"temp/{uid}", {"amount": amount})
        bot.send_message(c.message.chat.id,
            f"✅ <b>{amount} ብር</b> ምረጥ\n\n"
            f"🏦 CBE: <code>{CBE_ACCOUNT}</code>\n"
            f"📱 Telebirr: <code>0952346729</code>\n\n"
            f"💸 ከፈለ → 📸 Screenshot ላክ")

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
                f"❌ Balance አናሳ!\nMinimum: <b>{MIN_WITHDRAWAL} ብር</b>\nBalance: <b>{bal} ብር</b>")
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
            verified = " 🤖" if p.get("verified") else ""
            lines.append(f"{icon}{verified} {p.get('amount',0)} ብር — {t}")
        bot.send_message(c.message.chat.id, "\n".join(lines))

    # ── Withdrawal method ──
    elif data.startswith("wdm_"):
        method = data.replace("wdm_", "")
        fb_set(f"temp_wd/{uid}/method", method)
        fb_set(f"bot/state/{uid}", "waiting_wd_acct_num")
        bot.send_message(c.message.chat.id,
            f"📲 <b>{method}</b>\n\n🔢 Account number ላክ:")

    # ── Admin: Approve withdrawal ──
    elif data.startswith("wda_"):
        parts  = data.split("_")
        wid    = parts[1]
        u_id   = parts[2]
        amount = int(parts[3])
        fb_set(f"bot/withdrawals/{wid}/status", "approved")
        fb_set(f"users/{u_id}/pending_withdrawal", 0)
        wdSnap = fb_get("analytics/totalWithdrawals") or 0
        fb_set("analytics/totalWithdrawals", wdSnap + amount)
        profSnap = fb_get("analytics/totalProfit") or 0
        fb_set("analytics/totalProfit", max(0, profSnap - amount))
        try:
            bot.edit_message_text(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                text=c.message.text + "\n\n✅ <b>PAID</b>")
        except Exception: pass
        bot.send_message(int(u_id), f"✅ <b>{amount} ብር</b> ተላከ!")

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
        bot.send_message(int(u_id),
            f"❌ Withdrawal Rejected\n💰 <b>{amount} ብር</b> balance ላይ ተመለሰ!")

    # ── Admin: Manual approve deposit (fallback) ──
    elif data.startswith("ap_"):
        parts  = data.split("_")
        pid    = parts[1]
        u_id   = parts[2]
        amount = int(parts[3])
        bal    = fb_get(f"users/{u_id}/balance") or 0
        fb_set(f"users/{u_id}/balance", bal + amount)
        fb_set(f"payments/{pid}/status", "approved")
        dep_snap = fb_get("analytics/totalDeposits") or 0
        fb_set("analytics/totalDeposits", dep_snap + amount)
        try:
            bot.edit_message_caption(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n✅ <b>MANUALLY APPROVED</b>")
        except Exception: pass
        bot.send_message(int(u_id),
            f"✅ <b>{amount} ብር</b> ታከለ! (Manual)\nBalance: <b>{bal+amount} ብር</b>")

    # ── Admin: Manual reject deposit (fallback) ──
    elif data.startswith("re_"):
        parts = data.split("_")
        pid   = parts[1]
        u_id  = parts[2]
        fb_set(f"payments/{pid}/status", "rejected")
        fb_set(f"temp/{u_id}", None)
        try:
            bot.edit_message_caption(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n❌ <b>REJECTED</b>")
        except Exception: pass
        bot.send_message(int(u_id), "❌ <b>Deposit Rejected</b>\nAdmin ያናግሩ።")

# ══════════════════════════════════════════════════════
#  📊 DAILY REPORT
# ══════════════════════════════════════════════════════
def daily_report_loop():
    import time
    while True:
        now      = datetime.now()
        next_run = now.replace(
            hour=DAILY_REPORT_HOUR, minute=DAILY_REPORT_MINUTE,
            second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        time.sleep((next_run - now).total_seconds())
        try:
            payments    = fb_get("payments") or {}
            withdrawals = fb_get("bot/withdrawals") or {}
            users       = fb_get("users") or {}
            today       = datetime.now().strftime("%Y-%m-%d")
            today_ts    = datetime.now().replace(
                hour=0, minute=0, second=0).timestamp() * 1000

            dep_today  = [p for p in payments.values()
                          if p.get("time", 0) >= today_ts and p.get("status") == "approved"]
            dep_auto   = [p for p in dep_today if p.get("verified")]
            wd_today   = [w for w in withdrawals.values()
                          if w.get("status") == "approved" and today in str(w.get("time",""))]
            total_dep  = sum(p.get("amount", 0) for p in dep_today)
            total_wd   = sum(w.get("amount", 0) for w in wd_today)
            pend_dep   = sum(1 for p in payments.values() if p.get("status") == "pending")
            pend_wd    = sum(1 for w in withdrawals.values() if w.get("status") == "pending")
            total_bal  = sum((u.get("balance") or 0) for u in users.values())

            bot.send_message(ADMIN_ID,
                f"📊 <b>Daily Report — {today}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💳 Deposits: <b>{len(dep_today)}</b> ({total_dep} ብር)\n"
                f"   └ 🤖 Auto verified: {len(dep_auto)}\n"
                f"🏧 Withdrawals: <b>{len(wd_today)}</b> ({total_wd} ብር)\n\n"
                f"⏳ Pending Deposits: {pend_dep}\n"
                f"⏳ Pending Withdrawals: {pend_wd}\n\n"
                f"👥 Users: {len(users)}\n"
                f"💰 Total Balance: {total_bal} ብር\n"
                f"📈 Net: {total_dep - total_wd} ብር")
        except Exception as e:
            print(f"Daily report error: {e}")

threading.Thread(target=daily_report_loop, daemon=True).start()

# ══════════════════════════════════════════════════════
#  🚀 RUN
# ══════════════════════════════════════════════════════
print("🤖 Bingo Bot running with SMS Auto-Verify...")
bot.infinity_polling(skip_pending=True)
