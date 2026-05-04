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

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bingo Bot is running"

@flask_app.route("/sms", methods=["POST"])
def sms_webhook():
    try:
        sms_text = ""
        if flask_request.is_json:
            data = flask_request.get_json(force=True, silent=True) or {}
            sms_text = data.get("text", "") or data.get("sms", "") or data.get("message", "") or data.get("body", "")
        if not sms_text:
            sms_text = (flask_request.form.get("text", "") or
                       flask_request.form.get("sms", "") or
                       flask_request.form.get("body", "") or
                       flask_request.form.get("message", ""))
        if not sms_text:
            try:
                raw = flask_request.get_data(as_text=True)
                if raw:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(raw)
                    sms_text = (parsed.get("text", [""])[0] or
                               parsed.get("body", [""])[0] or
                               parsed.get("sms", [""])[0])
                if not sms_text:
                    sms_text = raw
            except:
                pass

        print(f"SMS Webhook received: {sms_text[:100] if sms_text else 'EMPTY'}")
        if not sms_text:
            return jsonify({"status": "ok"}), 200

        threading.Thread(target=handle_sms_from_webhook, args=(sms_text,), daemon=True).start()
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"SMS webhook error: {e}")
        return jsonify({"status": "ok"}), 200


def handle_sms_from_webhook(sms_text):
    try:
        refs = extract_refs_from_text(sms_text)
        if not refs:
            bot.send_message(ADMIN_ID,
                f"⚠️ <b>SMS ደረሰ ግን REF አልተገኘም</b>\n\n<code>{sms_text[:200]}</code>")
            return

        amount = extract_amount_from_sms(sms_text)

        for ref in refs:
            if is_dup_ref(ref):
                bot.send_message(ADMIN_ID, f"⚠️ Duplicate SMS REF: <code>{ref}</code>")
                return

        payments = fb_get("payments") or {}
        matched_pid = None
        matched_uid = None
        matched_ref = None

        for pid, pay in payments.items():
            if pay.get("status") != "pending":
                continue
            pay_ref = (pay.get("ref") or "").upper()
            if pay_ref in [r.upper() for r in refs]:
                matched_pid = pid
                matched_uid = str(pay.get("user_id"))
                matched_ref = pay_ref
                break

        if matched_pid and matched_uid:
            for ref in refs:
                save_ref(ref, matched_uid, amount)
            do_approve(matched_pid, matched_uid, amount, matched_ref, sms_text)
        else:
            for ref in refs:
                fb_set(f"bot/sms_pool/{ref.upper()}", {
                    "ref": ref.upper(),
                    "amount": amount,
                    "text": sms_text[:300],
                    "saved_at": datetime.now().timestamp(),
                })
            bot.send_message(ADMIN_ID,
                f"📥 <b>SMS ተቀበለ — Screenshot ይጠብቃል</b>\n\n"
                f"📋 REFs: {' | '.join(f'<code>{r}</code>' for r in refs)}\n"
                f"💰 Amount: {amount} ብር")

    except Exception as e:
        print(f"handle_sms_from_webhook error: {e}")
        bot.send_message(ADMIN_ID, f"❌ SMS processing error: {e}")


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

def extract_refs_from_text(text):
    if not text:
        return []
    refs = []
    cbe_url = re.search(r'/BranchReceipt/([A-Z0-9]{8,20})&', text, re.IGNORECASE)
    if cbe_url:
        r = cbe_url.group(1).upper()
        if r not in refs: refs.append(r)
    cbe_id = re.search(r'transaction\s*(?:ID|id)\s*:?\s*(FT[A-Z0-9]{6,16})', text, re.IGNORECASE)
    if cbe_id:
        r = cbe_id.group(1).upper()
        if r not in refs: refs.append(r)
    cbe_bank = re.search(r'bank\s+transaction\s+number\s+is\s+(FT[A-Z0-9]{6,16})', text, re.IGNORECASE)
    if cbe_bank:
        r = cbe_bank.group(1).upper()
        if r not in refs: refs.append(r)
    tel_num = re.search(r'(?<!bank\s)transaction\s+number\s+is\s+([A-Z0-9]{8,16})', text, re.IGNORECASE)
    if tel_num:
        r = tel_num.group(1).upper()
        if r not in refs: refs.append(r)
    tel_url = re.search(r'/receipt/([A-Z0-9]{8,16})', text, re.IGNORECASE)
    if tel_url:
        r = tel_url.group(1).upper()
        if r not in refs: refs.append(r)
    tel_am = re.search(r'የ[^\s]*ቁጥር[^\s]*\s+([A-Z0-9]{8,16})', text, re.IGNORECASE)
    if tel_am:
        r = tel_am.group(1).upper()
        if r not in refs: refs.append(r)
    for ft in re.findall(r'\b(FT[A-Z0-9]{6,16})\b', text, re.IGNORECASE):
        if ft.upper() not in refs: refs.append(ft.upper())
    for de in re.findall(r'\b(DE[A-Z0-9]{6,14})\b', text, re.IGNORECASE):
        if de.upper() not in refs: refs.append(de.upper())
    return refs

def extract_ref_from_text(text):
    refs = extract_refs_from_text(text)
    return refs[0] if refs else None

def extract_amount_from_sms(text):
    cbe = re.search(r'credited\s+with\s+ETB\s+([\d,]+\.?\d*)', text, re.IGNORECASE)
    if cbe: return float(cbe.group(1).replace(',', ''))
    tel_recv = re.search(r'received\s+ETB\s+([\d,]+\.?\d*)', text, re.IGNORECASE)
    if tel_recv: return float(tel_recv.group(1).replace(',', ''))
    tel_trans = re.search(r'transferred\s+ETB\s+([\d,]+\.?\d*)', text, re.IGNORECASE)
    if tel_trans: return float(tel_trans.group(1).replace(',', ''))
    cbe2 = re.search(r'Completed\s+ETB\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if cbe2: return float(cbe2.group(1).replace(',', ''))
    am = re.search(r'([\d,]+\.?\d*)\s*ብር', text)
    if am: return float(am.group(1).replace(',', ''))
    return 0.0

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def extract_refs_from_screenshot(file_id):
    try:
        file_info = bot.get_file(file_id)
        file_url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        response  = requests.get(file_url, timeout=15)
        import base64
        image_data = base64.b64encode(response.content).decode("utf-8")
        groq_response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                        {"type": "text", "text": "Extract ALL transaction reference numbers from this payment screenshot. Look for: FT followed by letters/numbers (CBE bank transaction), DE followed by letters/numbers (Telebirr transaction). There may be TWO reference numbers. Reply with ONLY the reference numbers separated by comma, nothing else. Example: DE49IZZB05,FT26124HX4GY. If not found, reply: NONE"}
                    ]
                }],
                "max_tokens": 100
            },
            timeout=30
        )
        result = groq_response.json()
        ref_text = result["choices"][0]["message"]["content"].strip()
        print(f"Groq REF text: {ref_text}")
        if ref_text == "NONE" or not ref_text:
            return []
        parts = [p.strip().upper() for p in ref_text.split(",")]
        refs  = []
        for part in parts:
            extracted = extract_refs_from_text(part)
            if extracted:
                for r in extracted:
                    if r not in refs: refs.append(r)
            elif re.match(r'^[A-Z0-9]{8,20}$', part):
                if part not in refs: refs.append(part)
        return refs
    except Exception as e:
        print(f"Groq OCR error: {e}")
        return []

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

def do_approve(pid, uid, amount, ref, sms_text=""):
    try:
        amount = int(amount) if amount else 0
        if amount <= 0:
            bot.send_message(ADMIN_ID,
                f"⚠️ Amount 0 ነው! Manual check:\n👤 <code>{uid}</code>\n📋 <code>{ref}</code>")
            return
        bal     = fb_get(f"users/{uid}/balance") or 0
        new_bal = bal + amount
        fb_set(f"users/{uid}/balance",     new_bal)
        fb_set(f"payments/{pid}/status",   "approved")
        fb_set(f"payments/{pid}/verified", True)
        fb_set(f"payments/{pid}/ref",      ref)
        fb_set(f"temp/{uid}",              None)
        save_ref(ref, uid, amount)
        dep_snap = fb_get("analytics/totalDeposits") or 0
        fb_set("analytics/totalDeposits", dep_snap + amount)
        try:
            bot.send_message(int(uid),
                f"✅ <b>Deposit Approved!</b>\n\n"
                f"💰 {amount} ብር ታከለ\n\n"
                f"💼 New Balance: <b>{new_bal} ብር</b>")
        except Exception as e:
            print(f"User notify error: {e}")
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

def is_bank_sms(text):
    if not text: return False
    t = text.lower()
    if "from: 127" in t: return True
    if "from: cbe" in t: return True
    if "ethio telecom" in t: return True
    if "credited with etb" in t: return True
    if "you have received etb" in t: return True
    if "received etb" in t: return True
    if "transferred etb" in t: return True
    if "transaction number is" in t: return True
    if "has been credited" in t: return True
    if "branchreceipt" in t: return True
    if "bank transaction number" in t: return True
    if re.search(r'\bFT[A-Z0-9]{6,16}\b', text, re.IGNORECASE): return True
    if re.search(r'\bDE[A-Z0-9]{6,14}\b', text, re.IGNORECASE): return True
    return False
    def process_screenshot(m):
    uid  = str(m.from_user.id)
    temp = fb_get(f"temp/{uid}")

    if not temp:
        bot.send_message(m.chat.id,
            "❗ መጀመሪያ <b>Deposit</b> ምረጥ → amount ምረጥ → ከዚያ screenshot ላክ")
        return

    amount  = temp.get("amount", 0)
    file_id = m.photo[-1].file_id if m.content_type == "photo" else m.document.file_id

    if is_dup_screenshot(file_id):
        bot.send_message(m.chat.id, "🚫 ይህ Screenshot አስቀድሞ ጥቅም ላይ ዋሏል!")
        fb_set(f"temp/{uid}", None)
        return

    if has_pending(uid):
        bot.send_message(m.chat.id, "⚠️ አስቀድሞ Pending Payment አለዎት!")
        return

    bot.send_message(m.chat.id, "🔍 Screenshot እየተነበበ ነው...")
    refs = extract_refs_from_screenshot(file_id)

    if not refs:
        retry_count = temp.get("retry_count", 0) + 1
        fb_set(f"temp/{uid}/retry_count", retry_count)
        if retry_count < 3:
            bot.send_message(m.chat.id,
                f"⚠️ Screenshot ጥራት የለውም — ድጋሚ ላክ\n\n📸 <b>ግልጽ የሆነ screenshot ላክ</b>")
        else:
            save_screenshot_hash(file_id, uid, amount)
            result = fb_push("payments", {
                "user_id":  uid,
                "display":  m.from_user.username or m.from_user.first_name or uid,
                "amount":   amount,
                "file_id":  file_id,
                "ref":      "",
                "status":   "pending",
                "time":     int(datetime.now().timestamp() * 1000),
                "verified": False,
            })
            if result:
                pid = result.key
                fb_set(f"temp/{uid}/pid", pid)
                fb_set(f"temp/{uid}/retry_count", 0)
                bot.send_message(m.chat.id, "📸 Screenshot ተቀብሏል!\n\n⏳ Admin እያረጋገጠ ነው...")
                try:
                    # ✅ Notification only — no buttons
                    bot.send_photo(ADMIN_ID, file_id,
                        caption=f"📸 <b>New Screenshot (REF አልተነበበም)</b>\n\n"
                                f"👤 {m.from_user.username or m.from_user.first_name} (<code>{uid}</code>)\n"
                                f"💰 {amount} ብር\n\n"
                                f"⚠️ Admin Panel ላይ ያረጋግጡ")
                except Exception:
                    pass
        return

    for ref in refs:
        if is_dup_ref(ref):
            bot.send_message(m.chat.id, f"🚫 ይህ ደረሰኝ አስቀድሞ ጥቅም ላይ ዋሏል!")
            fb_set(f"temp/{uid}", None)
            return

    save_screenshot_hash(file_id, uid, amount)
    fb_set(f"temp/{uid}/retry_count", 0)

    primary_ref = temp.get("ref", refs[0]).upper()
    if primary_ref not in refs:
        primary_ref = refs[0]

    result = fb_push("payments", {
        "user_id":  uid,
        "display":  m.from_user.username or m.from_user.first_name or uid,
        "amount":   amount,
        "file_id":  file_id,
        "ref":      primary_ref,
        "status":   "pending",
        "time":     int(datetime.now().timestamp() * 1000),
        "verified": False,
    })

    if not result:
        bot.send_message(m.chat.id, "❌ Error! እንደገና ሞክር")
        return

    pid = result.key
    fb_set(f"temp/{uid}/pid", pid)
    fb_set(f"temp/{uid}/ref", primary_ref)

    sms_pool = fb_get("bot/sms_pool") or {}
    matched_sms = None
    matched_sms_ref = None

    for ref in refs:
        if ref.upper() in sms_pool:
            matched_sms = sms_pool[ref.upper()]
            matched_sms_ref = ref.upper()
            break

    if matched_sms:
        fb_delete(f"bot/sms_pool/{matched_sms_ref}")
        for ref in refs:
            save_ref(ref, uid, matched_sms.get("amount", 0))
        do_approve(pid, uid, matched_sms.get("amount", 0), matched_sms_ref, matched_sms.get("text", ""))
    else:
        bot.send_message(m.chat.id, f"📸 Screenshot ተቀብሏል!\n\n⏳ እየተረጋገጠ ነው...")
        try:
            # ✅ Notification only — no buttons
            bot.send_photo(ADMIN_ID, file_id,
                caption=f"📸 <b>New Screenshot</b>\n\n"
                        f"👤 {m.from_user.username or m.from_user.first_name} (<code>{uid}</code>)\n"
                        f"💰 {amount} ብር\n"
                        f"📋 REFs: {' | '.join(f'<code>{r}</code>' for r in refs)}\n\n"
                        f"⚠️ Admin Panel ላይ Approve/Reject ያድርጉ")
        except Exception:
            pass


@bot.message_handler(content_types=["photo", "document"])
def handle_screenshot(m):
    threading.Thread(target=process_screenshot, args=(m,), daemon=True).start()

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
    if m.chat.id != ADMIN_ID: return
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
    if m.chat.id != ADMIN_ID: return
    payments = fb_get("payments") or {}
    pending  = [(pid, p) for pid, p in payments.items() if p.get("status") == "pending"]
    if not pending:
        bot.send_message(m.chat.id, "✅ ምንም pending የለም"); return
    lines = [f"⏳ <b>Pending ({len(pending)}):</b>\n"]
    for pid, p in pending[:10]:
        t = datetime.fromtimestamp(p.get("time", 0)/1000).strftime("%m/%d %H:%M") if p.get("time") else "—"
        lines.append(f"• {p.get('display','?')} — {p.get('amount',0)} ብር — {t}")
    bot.send_message(m.chat.id, "\n".join(lines))

@bot.message_handler(commands=["clearpending"])
def clear_pending(m):
    if m.chat.id != ADMIN_ID: return
    parts = m.text.split()
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Usage: /clearpending <user_id>"); return
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

ALLOWED_SMS_SENDERS = [ADMIN_ID]

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    uid   = str(m.from_user.id)
    text  = m.text.strip()
    state = fb_get(f"bot/state/{uid}")

    print(f"SENDER ID: {m.from_user.id} | USERNAME: {m.from_user.username} | TEXT: {text[:50]}")

    if m.from_user.id in ALLOWED_SMS_SENDERS and is_bank_sms(text):
        print(f"Bank SMS received from {m.from_user.id}: {text[:100]}")
        threading.Thread(target=handle_sms_from_webhook, args=(text,), daemon=True).start()
        return

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
        name = m.from_user.username or m.from_user.first_name
        # ✅ Notification only — no buttons
        bot.send_message(ADMIN_ID,
            f"🏧 <b>New Withdrawal</b>\n"
            f"👤 {name} (<code>{uid}</code>)\n"
            f"💰 {amount} ብር\n"
            f"📲 {method} — <code>{account}</code>\n\n"
            f"⚠️ Admin Panel ላይ Approve/Reject ያድርጉ")
        return

    send_menu(m.chat.id)

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

MATCH_TIMEOUT = 5 * 60

def timeout_checker():
    while True:
        try:
            now_ts   = datetime.now().timestamp()
            payments = fb_get("payments") or {}
            for pid, pay in list(payments.items()):
                if pay.get("status") != "pending": continue
                created = pay.get("time", 0) / 1000
                if now_ts - created < MATCH_TIMEOUT: continue
                uid     = str(pay.get("user_id"))
                amount  = pay.get("amount", 0)
                ref     = pay.get("ref", "")
                display = pay.get("display") or uid
                fb_set(f"payments/{pid}/status", "cancelled")
                fb_delete(f"temp/{uid}")
                if ref:
                    fb_delete(f"bot/sms_pool/{ref.upper()}")
                try:
                    bot.send_message(int(uid),
                        f"⏰ <b>Deposit Cancelled!</b>\n\n"
                        f"💰 {amount} ብር\n\n"
                        f"⚠️ SMS 5 ደቂቃ ውስጥ አልደረሰም\n\n"
                        f"እንደገና deposit ሞክር 👇")
                    send_menu(int(uid))
                except Exception: pass
                bot.send_message(ADMIN_ID,
                    f"⏰ <b>Timeout — Auto Cancelled</b>\n\n"
                    f"👤 {display} (<code>{uid}</code>)\n"
                    f"💰 {amount} ብር\n"
                    f"📋 REF: <code>{ref}</code>")
        except Exception as e:
            print(f"Timeout checker error: {e}")
        time.sleep(30)

threading.Thread(target=timeout_checker, daemon=True).start()

def daily_report_loop():
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
            today_ts    = datetime.now().replace(hour=0, minute=0, second=0).timestamp() * 1000
            dep_today = [p for p in payments.values()
                         if p.get("time", 0) >= today_ts and p.get("status") == "approved"]
            wd_today  = [w for w in withdrawals.values()
                         if w.get("status") == "approved" and today in str(w.get("time",""))]
            total_dep = sum(p.get("amount", 0) for p in dep_today)
            total_wd  = sum(w.get("amount", 0) for w in wd_today)
            pend_dep  = sum(1 for p in payments.values() if p.get("status") == "pending")
            total_bal = sum((u.get("balance") or 0) for u in users.values())
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
