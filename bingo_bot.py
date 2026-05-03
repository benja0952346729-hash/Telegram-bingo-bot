"""
╔══════════════════════════════════════════════════════════════════╗
║              BINGO PRO — TELEGRAM BOT (SMS WEBHOOK)             ║
║  Flow: SMS → REF | Screenshot → REF | Match → Auto Approve      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import re
import io
import json
import time
import hashlib
import threading
import requests
from datetime import datetime, timedelta
from PIL import Image

import firebase_admin
from firebase_admin import credentials, db as firebase_db

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from flask import Flask, request as flask_request, jsonify

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
ADMIN_ID         = 6883208728
WEBAPP_URL       = "https://game-production-7f86.up.railway.app"
FIREBASE_DB_URL  = "https://house-rent-app-3674a-default-rtdb.firebaseio.com/"
CBE_ACCOUNT      = "1000641057146"
CBE_ACCOUNT_LAST = "7146"
TELEBIRR_ACCOUNT = "0952346729"
MIN_WITHDRAWAL   = 50
MAX_WITHDRAWAL   = 5000
DAILY_REPORT_HOUR   = 20
DAILY_REPORT_MINUTE = 0

# ══════════════════════════════════════════════════════
#  FIREBASE
# ══════════════════════════════════════════════════════
_key = os.environ.get("FIREBASE_KEY", "")
if _key:
    cred = credentials.Certificate(json.loads(_key))
else:
    cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

def fb_get(path):
    try:
        return firebase_db.reference(path).get()
    except Exception as e:
        print(f"Firebase get error [{path}]: {e}")
        return None

def fb_set(path, value):
    try:
        firebase_db.reference(path).set(value)
    except Exception as e:
        print(f"Firebase set error [{path}]: {e}")

def fb_delete(path):
    try:
        firebase_db.reference(path).delete()
    except Exception as e:
        print(f"Firebase delete error [{path}]: {e}")

def fb_push(path, value):
    try:
        return firebase_db.reference(path).push(value)
    except Exception as e:
        print(f"Firebase push error [{path}]: {e}")
        return None

# ══════════════════════════════════════════════════════
#  BOT
# ══════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ══════════════════════════════════════════════════════
#  FLASK
# ══════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bingo Bot is running"

# ══════════════════════════════════════════════════════
#  SMS WEBHOOK — SMS Forwarder App ከዚህ ይልካል
# ══════════════════════════════════════════════════════
@flask_app.route("/sms", methods=["POST"])
def sms_webhook():
    """
    SMS Forwarder app ይህ endpoint ላይ SMS text ይልካል።
    Expected JSON: {"text": "SMS content here"}
    ወይም form data: text=...
    """
    try:
        # JSON ወይም form data ይቀበላል
        if flask_request.is_json:
            data = flask_request.get_json()
            sms_text = data.get("text", "") or data.get("sms", "") or data.get("message", "")
        else:
            sms_text = flask_request.form.get("text", "") or flask_request.form.get("sms", "")

        if not sms_text:
            return jsonify({"status": "error", "message": "No SMS text"}), 400

        print(f"SMS Webhook received: {sms_text[:100]}")

        # Threading ውስጥ ያስተናግዳል
        threading.Thread(target=handle_sms_from_webhook, args=(sms_text,), daemon=True).start()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"SMS webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_sms_from_webhook(sms_text):
    """SMS text ተቀብሎ REF ያወጣል → pending payment ያዛምዳል → approve"""
    try:
        # REF ማውጣት
        ref = extract_ref_from_text(sms_text)
        if not ref:
            bot.send_message(ADMIN_ID,
                f"⚠️ <b>SMS ደረሰ ግን REF አልተገኘም</b>\n\n<code>{sms_text[:200]}</code>")
            return

        # Amount ማውጣት
        amount = extract_amount_from_sms(sms_text)

        # Duplicate ref check
        if is_dup_ref(ref):
            bot.send_message(ADMIN_ID, f"⚠️ Duplicate SMS REF: <code>{ref}</code>")
            return

        print(f"SMS REF: {ref}, Amount: {amount}")

        # Pending payments ውስጥ REF ማዛመድ
        payments = fb_get("payments") or {}
        matched_pid = None
        matched_uid = None

        for pid, pay in payments.items():
            if pay.get("status") != "pending":
                continue
            pay_ref = (pay.get("ref") or "").upper()
            if pay_ref == ref.upper():
                matched_pid = pid
                matched_uid = str(pay.get("user_id"))
                break

        if matched_pid and matched_uid:
            # Match ተገኘ → Approve!
            do_approve(matched_pid, matched_uid, amount, ref, sms_text)
        else:
            # Screenshot ገና አልደረሰም → SMS ያስቀምጣል ይጠብቃል
            fb_set(f"bot/sms_pool/{ref.upper()}", {
                "ref": ref.upper(),
                "amount": amount,
                "text": sms_text[:300],
                "saved_at": datetime.now().timestamp(),
            })
            bot.send_message(ADMIN_ID,
                f"📥 <b>SMS ተቀበለ — Screenshot ይጠብቃል</b>\n\n"
                f"📋 REF: <code>{ref}</code>\n"
                f"💰 Amount: {amount} ብር")

    except Exception as e:
        print(f"handle_sms_from_webhook error: {e}")
        bot.send_message(ADMIN_ID, f"❌ SMS processing error: {e}")


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ══════════════════════════════════════════════════════
#  REF EXTRACTION
# ══════════════════════════════════════════════════════
def extract_ref_from_text(text):
    """
    ከ SMS ወይም ከ OCR text REF ያወጣል።
    CBE  → FT... (URL ውስጥ ወይም text ውስጥ)
    Telebirr → DE... (transaction number)
    """
    if not text:
        return None

    # CBE — URL ውስጥ: /BranchReceipt/FT26124GS887&41057146
    cbe_url = re.search(r'/BranchReceipt/([A-Z0-9]{8,20})&', text, re.IGNORECASE)
    if cbe_url:
        return cbe_url.group(1).upper()

    # CBE — transaction ID: FT...
    cbe_id = re.search(r'transaction\s*(?:ID|id)\s*:?\s*(FT[A-Z0-9]{6,16})', text, re.IGNORECASE)
    if cbe_id:
        return cbe_id.group(1).upper()

    # CBE — bank transaction number is FT...
    cbe_bank = re.search(r'bank\s+transaction\s+number\s+is\s+(FT[A-Z0-9]{6,16})', text, re.IGNORECASE)
    if cbe_bank:
        return cbe_bank.group(1).upper()

    # Telebirr — transaction number is DE...
    tel_num = re.search(r'transaction\s+number\s+is\s+([A-Z0-9]{8,16})', text, re.IGNORECASE)
    if tel_num:
        return tel_num.group(1).upper()

    # Telebirr Amharic — የግብይት ቁጥር
    tel_am = re.search(r'የ[^\s]*ቁጥር[^\s]*\s+([A-Z0-9]{8,16})', text, re.IGNORECASE)
    if tel_am:
        return tel_am.group(1).upper()

    # Telebirr receipt URL: /receipt/DE...
    tel_url = re.search(r'/receipt/([A-Z0-9]{8,16})', text, re.IGNORECASE)
    if tel_url:
        return tel_url.group(1).upper()

    # Fallback — FT... ወይም DE... standalone
    ft = re.search(r'\b(FT[A-Z0-9]{6,16})\b', text, re.IGNORECASE)
    if ft:
        return ft.group(1).upper()

    de = re.search(r'\b(D[A-Z][A-Z0-9]{6,14})\b', text, re.IGNORECASE)
    if de:
        return de.group(1).upper()

    return None


def extract_amount_from_sms(text):
    """SMS ከ amount ያወጣል"""
    # CBE: credited with ETB 400
    cbe = re.search(r'credited\s+with\s+ETB\s+([\d,]+\.?\d*)', text, re.IGNORECASE)
    if cbe:
        return float(cbe.group(1).replace(',', ''))

    # Telebirr received: You have received ETB 100.00
    tel_recv = re.search(r'received\s+ETB\s+([\d,]+\.?\d*)', text, re.IGNORECASE)
    if tel_recv:
        return float(tel_recv.group(1).replace(',', ''))

    # Telebirr transferred: transferred ETB 2.00
    tel_trans = re.search(r'transferred\s+ETB\s+([\d,]+\.?\d*)', text, re.IGNORECASE)
    if tel_trans:
        return float(tel_trans.group(1).replace(',', ''))

    # CBE notification: Completed ETB200.61
    cbe2 = re.search(r'Completed\s+ETB\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if cbe2:
        return float(cbe2.group(1).replace(',', ''))

    # Telebirr Amharic: 200.00 ብር
    am = re.search(r'([\d,]+\.?\d*)\s*ብር', text)
    if am:
        return float(am.group(1).replace(',', ''))

    return 0.0

# ══════════════════════════════════════════════════════
#  OCR — Screenshot ከ REF ያወጣል
# ══════════════════════════════════════════════════════
_ocr_reader = None
_ocr_lock   = threading.Lock()

def get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        with _ocr_lock:
            if _ocr_reader is None:
                try:
                    import easyocr
                    print("EasyOCR loading...")
                    _ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
                    print("EasyOCR ready!")
                except Exception as e:
                    print(f"EasyOCR load error: {e}")
                    _ocr_reader = None
    return _ocr_reader

def extract_ref_from_screenshot(file_id):
    """Screenshot ከ REF ያወጣል"""
    try:
        file_info = bot.get_file(file_id)
        file_url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        response  = requests.get(file_url, timeout=15)
        img       = Image.open(io.BytesIO(response.content))
        img = img.convert('L')
        w, h = img.size
        if w < 800:
            img = img.resize((w * 2, h * 2), Image.LANCZOS)
        reader = get_ocr_reader()
        if not reader:
            return None
        import numpy as np
        img_array = np.array(img)
        results   = reader.readtext(img_array, detail=0)
        full_text = " ".join(results)
        print(f"OCR text: {full_text[:300]}")
        return extract_ref_from_text(full_text)
    except Exception as e:
        print(f"OCR error: {e}")
        return None

# ══════════════════════════════════════════════════════
#  DUPLICATE CHECKS
# ══════════════════════════════════════════════════════
def hash_file(file_id):
    return hashlib.sha256(file_id.encode()).hexdigest()

def is_dup_screenshot(file_id):
    h    = hash_file(file_id)
    used = fb_get("bot/used_hashes") or {}
    return h in used

def save_screenshot_hash(file_id, uid, amount):
    h = hash_file(file_id)
    fb_set(f"bot/used_hashes/{h}", {
        "user_id": uid, "amount": amount, "time": datetime.now().isoformat()
    })

def is_dup_ref(ref):
    used = fb_get("bot/used_refs") or {}
    return ref.upper() in used

def save_ref(ref, uid, amount):
    fb_set(f"bot/used_refs/{ref.upper()}", {
        "user_id": uid, "amount": amount, "time": datetime.now().isoformat()
    })

def has_pending(uid):
    payments = fb_get("payments") or {}
    for p in payments.values():
        if str(p.get("user_id")) == uid and p.get("status") == "pending":
            return True
    return False

# ══════════════════════════════════════════════════════
#  APPROVE
# ══════════════════════════════════════════════════════
def do_approve(pid, uid, amount, ref, sms_text=""):
    """Payment approve ያደርጋል — amount ከ SMS ይወሰዳል"""
    try:
        amount = int(amount) if amount else 0
        if amount <= 0:
            bot.send_message(ADMIN_ID,
                f"⚠️ Amount 0 ነው! Manual check:\n👤 <code>{uid}</code>\n📋 <code>{ref}</code>")
            return

        bal     = fb_get(f"users/{uid}/balance") or 0
        new_bal = bal + amount

        fb_set(f"users/{uid}/balance",       new_bal)
        fb_set(f"payments/{pid}/status",     "approved")
        fb_set(f"payments/{pid}/verified",   True)
        fb_set(f"payments/{pid}/ref",        ref)
        fb_set(f"temp/{uid}",                None)

        save_ref(ref, uid, amount)

        dep_snap = fb_get("analytics/totalDeposits") or 0
        fb_set("analytics/totalDeposits", dep_snap + amount)

        # User notify
        try:
            bot.send_message(int(uid),
                f"✅ <b>Deposit Approved!</b>\n\n"
                f"💰 {amount} ብር ታከለ\n"
                f"📋 REF: <code>{ref}</code>\n\n"
                f"💼 New Balance: <b>{new_bal} ብር</b>")
        except Exception as e:
            print(f"User notify error: {e}")

        # Admin notify
        pay     = fb_get(f"payments/{pid}") or {}
        display = pay.get("display") or uid
        bot.send_message(ADMIN_ID,
            f"✅ <b>Auto Approved!</b>\n\n"
            f"👤 {display} (<code>{uid}</code>)\n"
            f"💰 {amount} ብር\n"
            f"📋 REF: <code>{ref}</code>")

    except Exception as e:
        print(f"do_approve error: {e}")
        bot.send_message(ADMIN_ID, f"❌ Approve error: {e}\nREF: {ref}")

# ══════════════════════════════════════════════════════
#  SCREENSHOT HANDLER
# ══════════════════════════════════════════════════════
def process_screenshot(m):
    uid  = str(m.from_user.id)
    temp = fb_get(f"temp/{uid}")

    if not temp:
        bot.send_message(m.chat.id,
            "❗ መጀመሪያ <b>Deposit</b> ምረጥ → amount ምረጥ → ከዚያ screenshot ላክ")
        return

    amount  = temp.get("amount", 0)
    file_id = m.photo[-1].file_id if m.content_type == "photo" else m.document.file_id

    # Duplicate screenshot check
    if is_dup_screenshot(file_id):
        bot.send_message(m.chat.id, "🚫 ይህ Screenshot አስቀድሞ ጥቅም ላይ ዋሏል!")
        fb_set(f"temp/{uid}", None)
        return

    # Pending check
    if has_pending(uid):
        bot.send_message(m.chat.id, "⚠️ አስቀድሞ Pending Payment አለዎት!")
        return

    save_screenshot_hash(file_id, uid, amount)
    bot.send_message(m.chat.id, "🔍 Screenshot እየተነበበ ነው...")

    # OCR → REF
    ref = extract_ref_from_screenshot(file_id)

    if not ref:
        # OCR ካልቻለ → user manually ይጽፋል
        fb_set(f"temp/{uid}/file_id", file_id)
        fb_set(f"bot/state/{uid}", "waiting_ref")
        bot.send_message(m.chat.id,
            f"📸 Screenshot ተቀብሏል!\n\n"
            f"⚠️ REF number ማውጣት አልቻለም\n\n"
            f"📋 <b>REF number ጻፍ:</b>\n"
            f"• CBE: <code>FT261241NS84</code>\n"
            f"• Telebirr: <code>DE33I1UOW7</code>")
        return

    if is_dup_ref(ref):
        bot.send_message(m.chat.id, f"🚫 REF <code>{ref}</code> አስቀድሞ ጥቅም ላይ ዋሏል!")
        fb_set(f"temp/{uid}", None)
        return

    # Payment Firebase ላይ ያስቀምጣል
    result = fb_push("payments", {
        "user_id":  uid,
        "display":  m.from_user.username or m.from_user.first_name or uid,
        "amount":   amount,
        "file_id":  file_id,
        "ref":      ref.upper(),
        "status":   "pending",
        "time":     int(datetime.now().timestamp() * 1000),
        "verified": False,
    })

    if not result:
        bot.send_message(m.chat.id, "❌ Error! እንደገና ሞክር")
        return

    pid = result.key
    fb_set(f"temp/{uid}/pid", pid)
    fb_set(f"temp/{uid}/ref", ref.upper())

    # SMS pool ውስጥ ተመሳሳይ REF አለ?
    sms_pool = fb_get("bot/sms_pool") or {}
    if ref.upper() in sms_pool:
        sms_data = sms_pool[ref.upper()]
        fb_delete(f"bot/sms_pool/{ref.upper()}")
        do_approve(pid, uid, sms_data.get("amount", 0), ref, sms_data.get("text", ""))
    else:
        # SMS ይጠብቃል
        bot.send_message(m.chat.id,
            f"📸 Screenshot ተቀብሏል!\n"
            f"📋 REF: <code>{ref}</code>\n\n"
            f"⏳ SMS verification እየጠበቀ ነው...")

        try:
            bot.send_photo(ADMIN_ID, file_id,
                caption=f"📸 New Screenshot\n"
                        f"👤 {m.from_user.username or m.from_user.first_name} (<code>{uid}</code>)\n"
                        f"💰 {amount} ብር\n"
                        f"📋 REF: <code>{ref}</code>")
        except Exception:
            pass


@bot.message_handler(content_types=["photo", "document"])
def handle_screenshot(m):
    threading.Thread(target=process_screenshot, args=(m,), daemon=True).start()

# ══════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════
def send_menu(chat_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎮 Play Game",
           web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={chat_id}")))
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


@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid     = str(m.chat.id)
    display = m.from_user.username or m.from_user.first_name or uid
    if not fb_get(f"users/{uid}/balance"):
        fb_set(f"users/{uid}/balance", 0)
    fb_set(f"users/{uid}/display",  display)
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


@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    if m.chat.id != ADMIN_ID:
        return
    users    = fb_get("users") or {}
    payments = fb_get("payments") or {}
    approved = [p for p in payments.values() if p.get("status") == "approved"]
    total_dep = sum(p.get("amount", 0) for p in approved)
    total_bal = sum((u.get("balance") or 0) for u in users.values())
    bot.send_message(m.chat.id,
        f"📊 <b>Stats</b>\n\n"
        f"👥 Users: {len(users)}\n"
        f"✅ Approved: {len(approved)}\n"
        f"💰 Total Deposits: {total_dep} ብር\n"
        f"💼 Total Balance: {total_bal} ብር")


@bot.message_handler(commands=["pending"])
def show_pending(m):
    if m.chat.id != ADMIN_ID:
        return
    payments = fb_get("payments") or {}
    pending  = [(pid, p) for pid, p in payments.items() if p.get("status") == "pending"]
    if not pending:
        bot.send_message(m.chat.id, "✅ ምንም pending የለም")
        return
    lines = [f"⏳ <b>Pending ({len(pending)}):</b>\n"]
    for pid, p in pending[:10]:
        t = datetime.fromtimestamp(p.get("time", 0)/1000).strftime("%m/%d %H:%M") if p.get("time") else "—"
        lines.append(f"• {p.get('display','?')} — {p.get('amount',0)} ብር — {t}")
    bot.send_message(m.chat.id, "\n".join(lines))


@bot.message_handler(commands=["clearpending"])
def clear_pending(m):
    if m.chat.id != ADMIN_ID:
        return
    parts = m.text.split()
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Usage: /clearpending <user_id>")
        return
    uid      = parts[1]
    fb_set(f"temp/{uid}", None)
    payments = fb_get("payments") or {}
    count    = 0
    for pid, pay in payments.items():
        if str(pay.get("user_id")) == uid and pay.get("status") == "pending":
            fb_set(f"payments/{pid}/status", "cancelled")
            count += 1
    bot.send_message(m.chat.id,
        f"✅ User <code>{uid}</code> cleared!\n📋 {count} pending cancelled.")

# ══════════════════════════════════════════════════════
#  TEXT HANDLER
# ══════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    uid   = str(m.from_user.id)
    text  = m.text.strip()
    state = fb_get(f"bot/state/{uid}")

    # Manual REF entry (OCR ካልቻለ)
    if state == "waiting_ref":
        ref = extract_ref_from_text(text)
        if not ref:
            clean = text.upper().strip()
            if re.match(r'^(FT|DE|D[A-Z])[A-Z0-9]{6,16}$', clean):
                ref = clean
        if not ref:
            bot.send_message(m.chat.id,
                "❌ REF format ትክክል አይደለም!\n"
                "• CBE: <code>FT261241NS84</code>\n"
                "• Telebirr: <code>DE33I1UOW7</code>\n\nእንደገና ላክ:")
            return
        if is_dup_ref(ref):
            bot.send_message(m.chat.id, f"🚫 REF <code>{ref}</code> አስቀድሞ ጥቅም ላይ ዋሏል!")
            fb_set(f"bot/state/{uid}", None)
            fb_set(f"temp/{uid}", None)
            return

        temp    = fb_get(f"temp/{uid}") or {}
        amount  = temp.get("amount", 0)
        file_id = temp.get("file_id", "")
        fb_set(f"bot/state/{uid}", None)

        result = fb_push("payments", {
            "user_id": uid,
            "display": m.from_user.username or m.from_user.first_name or uid,
            "amount":  amount,
            "file_id": file_id,
            "ref":     ref.upper(),
            "status":  "pending",
            "time":    int(datetime.now().timestamp() * 1000),
            "verified": False,
        })
        if not result:
            bot.send_message(m.chat.id, "❌ Error!")
            return

        pid = result.key
        fb_set(f"temp/{uid}/pid", pid)
        fb_set(f"temp/{uid}/ref", ref.upper())

        # SMS pool check
        sms_pool = fb_get("bot/sms_pool") or {}
        if ref.upper() in sms_pool:
            sms_data = sms_pool[ref.upper()]
            fb_delete(f"bot/sms_pool/{ref.upper()}")
            do_approve(pid, uid, sms_data.get("amount", 0), ref)
        else:
            bot.send_message(m.chat.id,
                f"✅ REF ተቀብሏል: <code>{ref}</code>\n\n⏳ SMS verification እየጠበቀ ነው...")
        return

    # Withdrawal amount
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

    # Withdrawal account number
    if state == "waiting_wd_acct_num":
        account = text
        amount  = fb_get(f"temp_wd/{uid}/amount") or 0
        method  = fb_get(f"temp_wd/{uid}/method") or "—"
        balance = fb_get(f"users/{uid}/balance") or 0
        fb_set(f"users/{uid}/balance",            balance - amount)
        fb_set(f"users/{uid}/pending_withdrawal", amount)
        result = fb_push("bot/withdrawals", {
            "user_id": uid,
            "display": m.from_user.username or m.from_user.first_name or uid,
            "amount":  amount,
            "method":  method,
            "account": account,
            "status":  "pending",
            "time":    datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        wid = result.key if result else "unknown"
        fb_set(f"bot/state/{uid}", None)
        fb_set(f"temp_wd/{uid}", None)
        bot.send_message(m.chat.id,
            f"✅ <b>Withdrawal Request ተልኳል!</b>\n\n"
            f"💰 {amount} ብር\n"
            f"📲 {method} — <code>{account}</code>\n\n"
            f"⏳ Admin ያስተናግዳቸዋል")
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

    send_menu(m.chat.id)

# ══════════════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    bot.answer_callback_query(c.id)
    uid  = str(c.from_user.id)
    data = c.data

    if data == "deposit":
        kb = InlineKeyboardMarkup(row_width=1)
        for a in [50, 100, 200, 500, 1000]:
            kb.add(InlineKeyboardButton(f"💳 {a} ብር", callback_data=f"pay_{a}"))
        bot.send_message(c.message.chat.id, "💳 <b>Amount ምረጥ:</b>", reply_markup=kb)

    elif data.startswith("pay_"):
        amount = int(data.split("_")[1])
        fb_set(f"temp/{uid}", {"amount": amount})
        bot.send_message(c.message.chat.id,
            f"✅ <b>{amount} ብር</b>\n\n"
            f"🏦 CBE: <code>{CBE_ACCOUNT}</code>\n"
            f"📱 Telebirr: <code>{TELEBIRR_ACCOUNT}</code>\n\n"
            f"💸 ከፍለህ → 📸 Screenshot ላክ")

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
        icons = {"approved": "✅", "rejected": "❌", "pending": "⏳", "cancelled": "🚫"}
        lines = ["📊 <b>ግብይት ታሪክ:</b>\n"]
        for p in user_txns[:10]:
            icon = icons.get(p.get("status"), "❓")
            t    = datetime.fromtimestamp(p.get("time", 0)/1000).strftime("%m/%d %H:%M") if p.get("time") else "—"
            lines.append(f"{icon} {p.get('amount',0)} ብር — {t}")
        bot.send_message(c.message.chat.id, "\n".join(lines))

    elif data.startswith("wdm_"):
        method = data.replace("wdm_", "")
        fb_set(f"temp_wd/{uid}/method", method)
        fb_set(f"bot/state/{uid}", "waiting_wd_acct_num")
        bot.send_message(c.message.chat.id, f"📲 <b>{method}</b>\n\n🔢 Account number ላክ:")

    elif data.startswith("wda_"):
        parts  = data.split("_")
        wid    = parts[1]; u_id = parts[2]; amount = int(parts[3])
        fb_set(f"bot/withdrawals/{wid}/status", "approved")
        fb_set(f"users/{u_id}/pending_withdrawal", 0)
        wdSnap = fb_get("analytics/totalWithdrawals") or 0
        fb_set("analytics/totalWithdrawals", wdSnap + amount)
        try:
            bot.edit_message_text(chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                text=c.message.text + "\n\n✅ <b>PAID</b>")
        except Exception: pass
        try:
            bot.send_message(int(u_id), f"✅ <b>{amount} ብር</b> ተላከ!")
        except Exception: pass

    elif data.startswith("wdr_"):
        parts  = data.split("_")
        wid    = parts[1]; u_id = parts[2]; amount = int(parts[3])
        fb_set(f"bot/withdrawals/{wid}/status", "rejected")
        bal = fb_get(f"users/{u_id}/balance") or 0
        fb_set(f"users/{u_id}/balance", bal + amount)
        fb_set(f"users/{u_id}/pending_withdrawal", 0)
        try:
            bot.edit_message_text(chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                text=c.message.text + "\n\n❌ <b>REJECTED</b>")
        except Exception: pass
        try:
            bot.send_message(int(u_id),
                f"❌ Withdrawal Rejected\n💰 <b>{amount} ብር</b> balance ላይ ተመለሰ!")
        except Exception: pass

    elif data.startswith("ap_"):
        # Manual admin approve
        parts  = data.split("_")
        pid    = parts[1]; u_id = parts[2]; amount = int(parts[3])
        bal    = fb_get(f"users/{u_id}/balance") or 0
        fb_set(f"users/{u_id}/balance", bal + amount)
        fb_set(f"payments/{pid}/status", "approved")
        dep_snap = fb_get("analytics/totalDeposits") or 0
        fb_set("analytics/totalDeposits", dep_snap + amount)
        try:
            bot.edit_message_caption(chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n✅ <b>MANUALLY APPROVED</b>")
        except Exception: pass
        try:
            bot.send_message(int(u_id),
                f"✅ <b>{amount} ብር</b> ታከለ!\nBalance: <b>{bal+amount} ብር</b>")
        except Exception: pass

    elif data.startswith("re_"):
        parts = data.split("_")
        pid   = parts[1]; u_id = parts[2]
        fb_set(f"payments/{pid}/status", "rejected")
        fb_set(f"temp/{u_id}", None)
        try:
            bot.edit_message_caption(chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n❌ <b>REJECTED</b>")
        except Exception: pass
        try:
            bot.send_message(int(u_id), "❌ <b>Deposit Rejected</b>")
        except Exception: pass

# ══════════════════════════════════════════════════════
#  DAILY REPORT
# ══════════════════════════════════════════════════════
def daily_report_loop():
    while True:
        now      = datetime.now()
        next_run = now.replace(
            hour=DAILY_REPORT_HOUR,
            minute=DAILY_REPORT_MINUTE,
            second=0, microsecond=0
        )
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
            dep_today = [p for p in payments.values()
                         if p.get("time", 0) >= today_ts and p.get("status") == "approved"]
            wd_today  = [w for w in withdrawals.values()
                         if w.get("status") == "approved" and today in str(w.get("time",""))]
            total_dep   = sum(p.get("amount", 0) for p in dep_today)
            total_wd    = sum(w.get("amount", 0) for w in wd_today)
            pend_dep    = sum(1 for p in payments.values() if p.get("status") == "pending")
            total_bal   = sum((u.get("balance") or 0) for u in users.values())
            bot.send_message(ADMIN_ID,
                f"📊 <b>Daily Report — {today}</b>\n\n"
                f"💳 Deposits: <b>{len(dep_today)}</b> ({total_dep} ብር)\n"
                f"🏧 Withdrawals: <b>{len(wd_today)}</b> ({total_wd} ብር)\n"
                f"⏳ Pending: {pend_dep}\n"
                f"👥 Users: {len(users)}\n"
                f"💰 Total Balance: {total_bal} ብር\n"
                f"📈 Net: {total_dep - total_wd} ብር")
        except Exception as e:
            print(f"Daily report error: {e}")

threading.Thread(target=daily_report_loop, daemon=True).start()

# ══════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════
print("Bingo Bot starting...")
while True:
    try:
        bot.remove_webhook()
        print("Bot polling started...")
        bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"Bot crashed: {e}")
        print("Restarting in 5 seconds...")
        time.sleep(5)
