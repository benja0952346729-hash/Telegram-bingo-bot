"""
╔══════════════════════════════════════════════════════════════════╗
║         BINGO PRO — TELEGRAM BOT (SERVER.JS COMPATIBLE)         ║
║  Backend: PostgreSQL via server.js REST API                      ║
║  Flow: SMS → REF | Screenshot → REF | Match → Auto Approve      ║
║  Features: Referral | Welcome Bonus (server gives) | Withdraw   ║
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

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from flask import Flask, request as flask_request, jsonify

# ══════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
ADMIN_ID    = 6883208728
WEBAPP_URL  = "https://game-production-7f86.up.railway.app"
SERVER      = os.environ.get("SERVER_URL", "https://admin-panel-production-b31a.up.railway.app")

MIN_WITHDRAWAL   = 50
MAX_WITHDRAWAL   = 5000
DAILY_REPORT_HOUR   = 20
DAILY_REPORT_MINUTE = 0
REMINDER_HOURS      = 24

REFERRAL_SMALL_COUNT = 20
REFERRAL_SMALL_AMT   = 100
REFERRAL_BIG_COUNT   = 100
REFERRAL_BIG_AMT     = 5000

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ══════════════════════════════════════════
# SERVER.JS API HELPERS
# ══════════════════════════════════════════
def fb_get(path):
    """Read from server.js /db-get endpoint"""
    try:
        r = requests.get(f"{SERVER}/db-get", params={"path": path}, timeout=5)
        return r.json()
    except:
        return None

def fb_set(path, value):
    """Write to server.js /db-set endpoint"""
    try:
        requests.post(f"{SERVER}/db-set",
            json={"path": path, "value": value}, timeout=5)
    except:
        pass

def fb_delete(path):
    fb_set(path, None)

def fb_push(path, value):
    """Push to server.js /db-push endpoint"""
    try:
        r = requests.post(f"{SERVER}/db-push",
            json={"path": path, "value": value}, timeout=5)
        data = r.json()
        class R: pass
        obj = R()
        obj.key = data.get("key", str(int(time.time() * 1000)))
        return obj
    except:
        class R: pass
        obj = R()
        obj.key = str(int(time.time() * 1000))
        return obj

def get_balance(uid):
    """Get user balance from server.js"""
    try:
        r = requests.get(f"{SERVER}/get-balance", params={"uid": uid}, timeout=5)
        return int(float(r.json().get("balance", 0) or 0))
    except:
        return 0

def update_balance(uid, amount, typ="add"):
    """Add or subtract balance via server.js"""
    try:
        r = requests.post(f"{SERVER}/update-balance",
            json={"uid": uid, "amount": amount, "type": typ}, timeout=5)
        return int(float(r.json().get("balance", 0) or 0))
    except:
        return 0

def ensure_user(uid, display):
    """Register user via server.js /user-state — server gives 20 ብር welcome bonus automatically"""
    try:
        r = requests.get(f"{SERVER}/user-state",
            params={"userId": uid, "firstName": display}, timeout=5)
        data = r.json()
        return data.get("isNew", False), int(float(data.get("balance", 0) or 0))
    except:
        return False, 0

def get_cbe_account():
    return fb_get("bot/settings/cbe_account") or ""

def get_telebirr_account():
    return fb_get("bot/settings/telebirr_account") or ""

# ══════════════════════════════════════════
# BOT + FLASK
# ══════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bingo Bot is running"

# ══════════════════════════════════════════
# SMS WEBHOOK
# ══════════════════════════════════════════
@flask_app.route("/sms", methods=["POST"])
def sms_webhook():
    try:
        sms_text = ""
        if flask_request.is_json:
            data = flask_request.get_json(force=True, silent=True) or {}
            sms_text = data.get("text","") or data.get("sms","") or data.get("message","") or data.get("body","")
        if not sms_text:
            sms_text = (flask_request.form.get("text","") or
                        flask_request.form.get("sms","") or
                        flask_request.form.get("body","") or
                        flask_request.form.get("message",""))
        if not sms_text:
            try:
                raw = flask_request.get_data(as_text=True)
                if raw:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(raw)
                    sms_text = (parsed.get("text",[""])[0] or
                                parsed.get("body",[""])[0] or
                                parsed.get("sms",[""])[0])
                if not sms_text:
                    sms_text = raw
            except:
                pass
        print(f"SMS received: {sms_text[:100] if sms_text else 'EMPTY'}")
        if not sms_text:
            return jsonify({"status": "ok"}), 200
        threading.Thread(target=handle_sms_from_webhook, args=(sms_text,), daemon=True).start()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"SMS webhook error: {e}")
        return jsonify({"status": "ok"}), 200


# ══════════════════════════════════════════
# BROADCAST ENDPOINT (called by server.js)
# ══════════════════════════════════════════
@flask_app.route("/broadcast", methods=["POST"])
def broadcast():
    photo_bytes = None
    text = ""
    if flask_request.content_type and "multipart" in flask_request.content_type:
        text = flask_request.form.get("text", "")
        photo_file = flask_request.files.get("photo")
        if photo_file:
            photo_bytes = photo_file.read()
        if not photo_bytes:
            photo_url = flask_request.form.get("photo_url", "")
            if photo_url:
                try:
                    r = requests.get(photo_url, timeout=10)
                    if r.status_code == 200:
                        photo_bytes = r.content
                except Exception as e:
                    print(f"Photo URL download error: {e}")
    else:
        data = flask_request.get_json() or {}
        text = data.get("text", "")

    # Get all real users from server.js
    try:
        r = requests.get(f"{SERVER}/game-state", timeout=10)
        display_names = r.json().get("displayNames", {})
    except:
        display_names = {}

    sent = 0
    for uid in display_names.keys():
        if not str(uid).isdigit():
            continue
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🎮 Play Now",
                   web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
            if photo_bytes:
                bot.send_photo(int(uid), io.BytesIO(photo_bytes),
                    caption=text, reply_markup=kb)
            else:
                bot.send_message(int(uid), text, reply_markup=kb)
            sent += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"Broadcast error {uid}: {e}")

    return jsonify({"ok": True, "msg": f"✅ {sent} users ተላከ!"})


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

threading.Thread(target=run_flask, daemon=True).start()


# ══════════════════════════════════════════
# REF / AMOUNT EXTRACTORS
# ══════════════════════════════════════════
def extract_refs_from_text(text):
    if not text: return []
    refs = []
    patterns = [
        r'/BranchReceipt/([A-Z0-9]{8,20})&',
        r'transaction\s*(?:ID|id)\s*:?\s*(FT[A-Z0-9]{6,16})',
        r'bank\s+transaction\s+number\s+is\s+(FT[A-Z0-9]{6,16})',
        r'(?<!bank\s)transaction\s+number\s+is\s+([A-Z0-9]{8,16})',
        r'/receipt/([A-Z0-9]{8,16})',
        r'የ[^\s]*ቁጥር[^\s]*\s+([A-Z0-9]{8,16})',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            r = m.group(1).upper()
            if r not in refs: refs.append(r)
    for ft in re.findall(r'\b(FT[A-Z0-9]{6,16})\b', text, re.IGNORECASE):
        if ft.upper() not in refs: refs.append(ft.upper())
    for de in re.findall(r'\b(DE[A-Z0-9]{6,14})\b', text, re.IGNORECASE):
        if de.upper() not in refs: refs.append(de.upper())
    return refs

def extract_amount_from_sms(text):
    patterns = [
        r'credited\s+with\s+ETB\s+([\d,]+\.?\d*)',
        r'received\s+ETB\s+([\d,]+\.?\d*)',
        r'transferred\s+ETB\s+([\d,]+\.?\d*)',
        r'Completed\s+ETB\s*([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)\s*ብር',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m: return float(m.group(1).replace(',', ''))
    return 0.0

def is_bank_sms(text):
    if not text: return False
    t = text.lower()
    keywords = ["from: 127","from: cbe","ethio telecom","credited with etb",
                "you have received etb","received etb","transferred etb",
                "transaction number is","has been credited","branchreceipt",
                "bank transaction number"]
    if any(k in t for k in keywords): return True
    if re.search(r'\bFT[A-Z0-9]{6,16}\b', text, re.IGNORECASE): return True
    if re.search(r'\bDE[A-Z0-9]{6,14}\b', text, re.IGNORECASE): return True
    return False

def is_dup_ref(ref):
    used = fb_get("bot/used_refs") or {}
    return ref.upper() in used

def save_ref(ref, uid, amount):
    fb_set(f"bot/used_refs/{ref.upper()}",
           {"user_id": uid, "amount": amount, "time": datetime.now().isoformat()})

def is_dup_screenshot(file_id):
    h = hashlib.sha256(file_id.encode()).hexdigest()
    used = fb_get("bot/used_hashes") or {}
    return h in used

def save_screenshot_hash(file_id, uid, amount):
    h = hashlib.sha256(file_id.encode()).hexdigest()
    fb_set(f"bot/used_hashes/{h}",
           {"user_id": uid, "amount": amount, "time": datetime.now().isoformat()})

def has_pending(uid):
    payments = fb_get("payments") or {}
    for p in payments.values():
        if not isinstance(p, dict): continue
        if str(p.get("user_id")) == uid and p.get("status") == "pending":
            return True
    return False


# ══════════════════════════════════════════
# SMS HANDLER
# ══════════════════════════════════════════
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
        matched_pid = matched_uid = matched_ref = None

        for pid, pay in payments.items():
            if not isinstance(pay, dict): continue
            if pay.get("status") != "pending": continue
            pay_ref = (pay.get("ref") or "").upper()
            if pay_ref in [r.upper() for r in refs]:
                matched_pid = pid
                matched_uid = str(pay.get("user_id"))
                matched_ref = pay_ref
                break

        if matched_pid and matched_uid:
            for ref in refs: save_ref(ref, matched_uid, amount)
            do_approve(matched_pid, matched_uid, amount, matched_ref, sms_text)
            return

        photo_pool = fb_get("bot/photo_pool") or {}
        matched_photo = matched_photo_ref = None
        for ref in refs:
            if ref.upper() in photo_pool:
                matched_photo = photo_pool[ref.upper()]
                matched_photo_ref = ref.upper()
                break

        if matched_photo:
            for r in (matched_photo.get("all_refs") or [matched_photo_ref]):
                fb_delete(f"bot/photo_pool/{r.upper()}")
            for ref in refs: save_ref(ref, matched_photo["uid"], amount)
            do_approve(matched_photo["pid"], matched_photo["uid"], amount, matched_photo_ref, sms_text)
        else:
            for ref in refs:
                fb_set(f"bot/sms_pool/{ref.upper()}", {
                    "ref": ref.upper(), "amount": amount,
                    "text": sms_text[:300],
                    "saved_at": datetime.now().timestamp(),
                    "all_refs": refs,
                })
            bot.send_message(ADMIN_ID,
                f"📥 <b>SMS ተቀበለ — Screenshot ይጠብቃል</b>\n\n"
                f"📋 REFs: {' | '.join(f'<code>{r}</code>' for r in refs)}\n"
                f"💰 {amount} ብር")
    except Exception as e:
        print(f"handle_sms_from_webhook error: {e}")
        bot.send_message(ADMIN_ID, f"❌ SMS processing error: {e}")


# ══════════════════════════════════════════
# GROQ OCR
# ══════════════════════════════════════════
def extract_refs_from_screenshot(file_id):
    try:
        import base64
        file_info = bot.get_file(file_id)
        file_url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        response  = requests.get(file_url, timeout=15)
        image_data = base64.b64encode(response.content).decode("utf-8")
        groq_response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
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
        result   = groq_response.json()
        ref_text = result["choices"][0]["message"]["content"].strip()
        print(f"Groq REF: {ref_text}")
        if ref_text == "NONE" or not ref_text: return []
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


# ══════════════════════════════════════════
# APPROVE DEPOSIT
# ══════════════════════════════════════════
def do_approve(pid, uid, amount, ref, sms_text=""):
    try:
        amount = int(amount) if amount else 0
        if amount <= 0:
            bot.send_message(ADMIN_ID,
                f"⚠️ Amount 0 ነው! Manual check:\n👤 <code>{uid}</code>\n📋 <code>{ref}</code>")
            return

        # ✅ Update balance via server.js
        new_bal = update_balance(uid, amount, "add")

        fb_set(f"payments/{pid}/status", "approved")
        fb_set(f"payments/{pid}/verified", True)
        fb_set(f"payments/{pid}/ref", ref)
        fb_delete(f"temp/{uid}")
        fb_set(f"users/{uid}/last_activity", datetime.now().timestamp())
        save_ref(ref, uid, amount)

        # Analytics via server.js
        try:
            requests.post(f"{SERVER}/db-set",
                json={"path": "analytics/totalDeposits",
                      "value": (fb_get("analytics/totalDeposits") or 0) + amount},
                timeout=5)
        except: pass

        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🎮 Play Game",
                   web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
            kb.add(
                InlineKeyboardButton("💳 Deposit", callback_data="deposit"),
                InlineKeyboardButton("💰 Balance", callback_data="balance")
            )
            kb.add(
                InlineKeyboardButton("🏧 Withdraw", callback_data="withdraw"),
                InlineKeyboardButton("📊 History",  callback_data="history")
            )
            bot.send_message(int(uid),
                f"✅ <b>Deposit Approved!</b>\n\n"
                f"💰 {amount} ብር ታከለ\n"
                f"💼 New Balance: <b>{new_bal} ብር</b>",
                reply_markup=kb)
        except Exception as e:
            print(f"User notify error: {e}")

        pay = fb_get(f"payments/{pid}") or {}
        display = pay.get("display") or uid
        bot.send_message(ADMIN_ID,
            f"✅ <b>Auto Approved!</b>\n\n"
            f"👤 {display} (<code>{uid}</code>)\n"
            f"💰 {amount} ብር\n"
            f"📋 REF: <code>{ref}</code>")

    except Exception as e:
        print(f"do_approve error: {e}")
        bot.send_message(ADMIN_ID, f"❌ Approve error: {e}\nREF: {ref}")


# ══════════════════════════════════════════
# SCREENSHOT HANDLER
# ══════════════════════════════════════════
def process_screenshot(m):
    uid = str(m.from_user.id)
    raw_temp = fb_get(f"temp/{uid}")
    temp = None
    amount = 0

    if isinstance(raw_temp, dict):
        temp = raw_temp
        amount = int(float(raw_temp.get("amount", 0) or 0))
    elif isinstance(raw_temp, (int, float)):
        amount = int(float(raw_temp))
        temp = {"amount": amount}
    elif isinstance(raw_temp, str):
        try:
            amount = int(float(raw_temp))
            temp = {"amount": amount}
        except: pass

    if temp and not temp.get("retry_count"):
        rc = fb_get(f"temp/{uid}/retry_count")
        temp["retry_count"] = int(rc) if rc else 0

    if not amount:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 Deposit አድርግ", callback_data="deposit"))
        bot.send_message(m.chat.id,
            "❗ <b>መጀመሪያ Deposit ምረጥ!</b>\n\n"
            "👇 Deposit ተጫን → Amount ምረጥ → ከዚያ Screenshot ላክ",
            reply_markup=kb)
        return

    file_id = m.photo[-1].file_id if m.content_type == "photo" else m.document.file_id

    if is_dup_screenshot(file_id):
        bot.send_message(m.chat.id, "🚫 ይህ Screenshot አስቀድሞ ጥቅም ላይ ዋሏል!")
        fb_set(f"temp/{uid}", None)
        return

    if has_pending(uid):
        bot.send_message(m.chat.id, "⚠️ አስቀድሞ Pending Payment አለዎት!\n\nAdmin እየተጠባበቅ ነው...")
        return

    bot.send_message(m.chat.id, "🔍 Screenshot እየተነበበ ነው...")
    refs = extract_refs_from_screenshot(file_id)

    if not refs:
        retry_count = temp.get("retry_count", 0) + 1
        _t = fb_get(f"temp/{uid}") or {}
        if not isinstance(_t, dict): _t = {"amount": amount}
        _t["retry_count"] = retry_count
        fb_set(f"temp/{uid}", _t)
        if retry_count < 3:
            bot.send_message(m.chat.id,
                f"⚠️ Screenshot ጥራት የለውም — ድጋሚ ላክ ({retry_count}/3)\n\n"
                f"📸 <b>ግልጽ የሆነ screenshot ላክ</b>")
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
                _t2 = fb_get(f"temp/{uid}") or {}
                if not isinstance(_t2, dict): _t2 = {}
                _t2["pid"] = pid
                _t2["retry_count"] = 0
                fb_set(f"temp/{uid}", _t2)
                bot.send_message(m.chat.id, "📸 Screenshot ተቀብሏል!\n\n⏳ Admin እያረጋገጠ ነው...")
                try:
                    bot.send_photo(ADMIN_ID, file_id,
                        caption=f"📸 <b>New Screenshot (REF አልተነበበም)</b>\n\n"
                                f"👤 {m.from_user.username or m.from_user.first_name} (<code>{uid}</code>)\n"
                                f"💰 {amount} ብር\n\n⚠️ Admin Panel ላይ ያረጋግጡ")
                except: pass
        return

    for ref in refs:
        if is_dup_ref(ref):
            bot.send_message(m.chat.id, "🚫 ይህ ደረሰኝ አስቀድሞ ጥቅም ላይ ዋሏል!")
            fb_set(f"temp/{uid}", None)
            return

    save_screenshot_hash(file_id, uid, amount)
    _t3 = fb_get(f"temp/{uid}") or {}
    if not isinstance(_t3, dict): _t3 = {}
    _t3["retry_count"] = 0
    fb_set(f"temp/{uid}", _t3)

    primary_ref = temp.get("ref", refs[0]).upper()
    if primary_ref not in refs: primary_ref = refs[0]

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
    _t4 = fb_get(f"temp/{uid}") or {}
    if not isinstance(_t4, dict): _t4 = {}
    _t4["pid"] = pid
    _t4["ref"] = primary_ref
    fb_set(f"temp/{uid}", _t4)

    sms_pool = fb_get("bot/sms_pool") or {}
    matched_sms = matched_sms_ref = None
    for ref in refs:
        if ref.upper() in sms_pool:
            matched_sms     = sms_pool[ref.upper()]
            matched_sms_ref = ref.upper()
            break

    if matched_sms:
        for r in (matched_sms.get("all_refs") or [matched_sms_ref]):
            fb_delete(f"bot/sms_pool/{r.upper()}")
        for ref in refs: save_ref(ref, uid, matched_sms.get("amount", 0))
        do_approve(pid, uid, matched_sms.get("amount", 0), matched_sms_ref, matched_sms.get("text", ""))
    else:
        for ref in refs:
            fb_set(f"bot/photo_pool/{ref.upper()}", {
                "ref":      ref.upper(),
                "all_refs": refs,
                "pid":      pid,
                "uid":      uid,
                "amount":   amount,
                "file_id":  file_id,
                "saved_at": datetime.now().timestamp(),
            })
        bot.send_message(m.chat.id, "📸 Screenshot ተቀብሏል!\n\n⏳ እየተረጋገጠ ነው...")
        try:
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("✅ Approve", callback_data=f"ap_{pid}_{uid}_{amount}"),
                InlineKeyboardButton("❌ Reject",  callback_data=f"re_{pid}_{uid}")
            )
            bot.send_photo(ADMIN_ID, file_id,
                caption=f"📸 <b>New Screenshot</b>\n\n"
                        f"👤 {m.from_user.username or m.from_user.first_name} (<code>{uid}</code>)\n"
                        f"💰 {amount} ብር\n"
                        f"📋 REFs: {' | '.join(f'<code>{r}</code>' for r in refs)}\n\n"
                        f"⏳ SMS እየጠበቀ ነው...",
                reply_markup=kb)
        except: pass


@bot.message_handler(content_types=["photo", "document"])
def handle_screenshot(m):
    threading.Thread(target=process_screenshot, args=(m,), daemon=True).start()


# ══════════════════════════════════════════
# REFERRAL SYSTEM
# ══════════════════════════════════════════
def get_referral_link(uid):
    bot_info = bot.get_me()
    return f"https://t.me/{bot_info.username}?start=ref{uid}"

def handle_referral_registration(new_uid, referrer_uid):
    try:
        if str(new_uid) == str(referrer_uid): return
        already = fb_get(f"users/{new_uid}/referred_by")
        if already: return
        fb_set(f"users/{new_uid}/referred_by", str(referrer_uid))
        fb_push(f"referrals/{referrer_uid}/list",
                {"uid": str(new_uid), "time": datetime.now().isoformat()})
        old_count = fb_get(f"referrals/{referrer_uid}/count") or 0
        new_count = old_count + 1
        fb_set(f"referrals/{referrer_uid}/count", new_count)
        print(f"Referral: {new_uid} referred by {referrer_uid} — total: {new_count}")
        if new_count == REFERRAL_SMALL_COUNT:
            _give_referral_bonus(referrer_uid, REFERRAL_SMALL_AMT, new_count)
        elif new_count == REFERRAL_BIG_COUNT:
            _give_referral_bonus(referrer_uid, REFERRAL_BIG_AMT, new_count)
        try:
            bot.send_message(int(referrer_uid),
                f"🎉 <b>አዲስ ሰው አስገባህ!</b>\n\n"
                f"👥 ጠቅላላ Referral: <b>{new_count}</b>\n\n"
                + (f"⭐ {REFERRAL_SMALL_COUNT - new_count} ሰው ሲጨምር 💰 {REFERRAL_SMALL_AMT} ብር ታገኛለህ!"
                   if new_count < REFERRAL_SMALL_COUNT
                   else f"⭐ {REFERRAL_BIG_COUNT - new_count} ሰው ሲጨምር 💰 {REFERRAL_BIG_AMT} ብር ታገኛለህ!"
                   if new_count < REFERRAL_BIG_COUNT
                   else "🏆 ትልቅ ሽልማት አሸነፍህ!"))
        except Exception as e:
            print(f"Referral notify error: {e}")
    except Exception as e:
        print(f"handle_referral_registration error: {e}")

def _give_referral_bonus(referrer_uid, bonus_amount, count):
    try:
        new_bal = update_balance(referrer_uid, bonus_amount, "add")
        fb_push(f"referrals/{referrer_uid}/bonuses",
                {"amount": bonus_amount, "count": count, "time": datetime.now().isoformat()})
        bot.send_message(int(referrer_uid),
            f"🏆 <b>Referral Bonus!</b>\n\n"
            f"👥 {count} ሰው አስገባህ!\n"
            f"💰 <b>+{bonus_amount} ብር</b> ታከለ!\n"
            f"💼 አዲስ Balance: <b>{new_bal} ብር</b>")
        bot.send_message(ADMIN_ID,
            f"🏆 <b>Referral Bonus Paid</b>\n"
            f"👤 <code>{referrer_uid}</code>\n"
            f"👥 {count} referrals\n"
            f"💰 {bonus_amount} ብር")
    except Exception as e:
        print(f"_give_referral_bonus error: {e}")

def _show_referral(chat_id, uid):
    try:
        ref_link  = get_referral_link(uid)
        ref_count = fb_get(f"referrals/{uid}/count") or 0
        bonuses   = fb_get(f"referrals/{uid}/bonuses") or {}
        total_bonus_earned = sum(
            b.get("amount", 0) for b in bonuses.values() if isinstance(b, dict)
        )
        if ref_count < REFERRAL_SMALL_COUNT:
            needed = REFERRAL_SMALL_COUNT - ref_count; next_amt = REFERRAL_SMALL_AMT
            progress = int((ref_count / REFERRAL_SMALL_COUNT) * 10)
        elif ref_count < REFERRAL_BIG_COUNT:
            needed = REFERRAL_BIG_COUNT - ref_count; next_amt = REFERRAL_BIG_AMT
            progress = int(((ref_count - REFERRAL_SMALL_COUNT) / (REFERRAL_BIG_COUNT - REFERRAL_SMALL_COUNT)) * 10)
        else:
            needed = 0; next_amt = 0; progress = 10
        bar = "🟩" * progress + "⬜" * (10 - progress)
        text = (
            f"👥 <b>Referral Program</b>\n\n"
            f"🔗 <b>የኔ Link፡</b>\n<code>{ref_link}</code>\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 ያስገባሃቸው ሰዎች: <b>{ref_count}</b>\n"
            f"💰 ያገኘሃቸው Bonus: <b>{total_bonus_earned} ብር</b>\n\n"
            f"🏆 <b>ሽልማቶች፡</b>\n\n"
            f"🥈 <b>{REFERRAL_SMALL_COUNT} ሰው</b> → 💰 <b>{REFERRAL_SMALL_AMT} ብር</b>\n"
            f"🥇 <b>{REFERRAL_BIG_COUNT} ሰው</b> → 💰 <b>{REFERRAL_BIG_AMT} ብር</b>\n\n"
            f"📈 <b>Progress:</b> {bar}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"💡 Link share አድርግ — ሲመዘገቡ ቀጥታ ትቆጠርላቸዋል!"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔗 Link ተቀዳ", switch_inline_query=ref_link))
        bot.send_message(chat_id, text, reply_markup=kb)
    except Exception as e:
        print(f"_show_referral error: {e}")
        bot.send_message(chat_id, "❌ Error! እንደገና ሞክር")


# ══════════════════════════════════════════
# MENU
# ══════════════════════════════════════════
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
    kb.add(InlineKeyboardButton("👥 Referral", callback_data="referral"))
    bot.send_message(chat_id,
        "🎮 <b>Bingo Pro</b>\n\n"
        "🎁 <b>አሁን ያሉ Bonuses፡</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "👋 Welcome Bonus  → <b>+20 ብር</b>\n"
        "👥 Referral       → <b>100 እስከ 5000 ብር</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "🏆 Prize Pool — <b>80% ለአሸናፊ!</b>\n\n"
        "👇 ምረጥ፡",
        reply_markup=kb)


# ══════════════════════════════════════════
# /start COMMAND
# ══════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = str(m.chat.id)
    args = m.text.split()
    referrer_uid = None
    display = m.from_user.first_name or m.from_user.username or uid

    # Deep link: deposit amount
    if len(args) > 1 and args[1].startswith("deposit_"):
        try:
            amount = int(args[1].split("_")[1])
            fb_set(f"temp/{uid}", {"amount": amount, "retry_count": 0})
            bot.send_message(m.chat.id,
                f"✅ <b>{amount} ብር Deposit</b>\n"
                f"🏦 CBE: <code>{get_cbe_account()}</code>\n"
                f"📱 Telebirr: <code>{get_telebirr_account()}</code>\n\n"
                f"💸 ከፍለህ → 📸 Screenshot ላክ")
        except: pass
        return

    # Deep link: withdraw
    if len(args) > 1 and args[1].startswith("withdraw"):
        bal = get_balance(uid)
        if bal < MIN_WITHDRAWAL:
            bot.send_message(m.chat.id,
                f"❌ Balance አናሳ!\nMinimum: <b>{MIN_WITHDRAWAL} ብር</b>\nBalance: <b>{bal} ብር</b>")
            return
        fb_set(f"botstate_{uid}", "waiting_wd_amount")
        bot.send_message(m.chat.id,
            f"🏧 <b>Withdrawal</b>\n💰 Balance: <b>{bal} ብር</b>\n\nምን ያህል ብር? ቁጥር ላክ:")
        return

    # Referral link
    if len(args) > 1 and args[1].startswith("ref"):
        referrer_uid = args[1][3:]

    # ✅ Register user — server.js gives 20 ብር welcome bonus automatically via isNew check
    is_new, balance = ensure_user(uid, display)

    if is_new:
        # Store extra user info for referral/reminder system
        fb_set(f"users/{uid}/display",       display)
        fb_set(f"users/{uid}/username",      display)
        fb_set(f"users/{uid}/joined_at",     datetime.now().isoformat())
        fb_set(f"users/{uid}/last_activity", datetime.now().timestamp())

        # Welcome message
        bot.send_message(m.chat.id,
            f"🎁 <b>እንኳን ደህና መጣህ {display}!</b>\n\n"
            f"ወደ Bingo Pro እንኳን ደህና መጣህ! 🎮\n\n"
            f"🎉 <b>+20 ብር</b> Welcome Bonus ታከለ!\n\n"
            f"▶️ አሁን መጫወት ትችላለህ!")

        if referrer_uid:
            threading.Thread(
                target=handle_referral_registration,
                args=(uid, referrer_uid), daemon=True
            ).start()

        try:
            bot.send_message(ADMIN_ID,
                f"👤 <b>አዲስ User!</b>\n"
                f"Name: {display}\n"
                f"ID: <code>{uid}</code>"
                + (f"\nRef by: <code>{referrer_uid}</code>" if referrer_uid else ""))
        except: pass
    else:
        # Update display name
        fb_set(f"users/{uid}/display",  display)
        fb_set(f"users/{uid}/username", display)
        ensure_user(uid, display)

    send_menu(m.chat.id)


# ══════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════
@bot.message_handler(commands=["balance"])
def cmd_balance(m):
    uid = str(m.chat.id)
    bal = get_balance(uid)
    pending_wd = fb_get(f"users/{uid}/pending_withdrawal") or 0
    text = f"💰 <b>Balance: {bal} ብር</b>"
    if pending_wd:
        text += f"\n⏳ Pending Withdrawal: {pending_wd} ብር"
    bot.send_message(m.chat.id, text)

@bot.message_handler(commands=["referral"])
def cmd_referral(m):
    _show_referral(m.chat.id, str(m.from_user.id))

@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if m.chat.id != ADMIN_ID: return
    cbe = get_cbe_account()
    tel = get_telebirr_account()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"✏️ CBE: {cbe}",      callback_data="set_cbe"))
    kb.add(InlineKeyboardButton(f"✏️ Telebirr: {tel}", callback_data="set_telebirr"))
    bot.send_message(m.chat.id,
        f"⚙️ <b>Admin Panel</b>\n\n"
        f"🏦 CBE: <code>{cbe}</code>\n"
        f"📱 Telebirr: <code>{tel}</code>",
        reply_markup=kb)

@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    if m.chat.id != ADMIN_ID: return
    try:
        r = requests.get(f"{SERVER}/health", timeout=5)
        health = r.json()
        analytics_r = requests.get(f"{SERVER}/game-state", timeout=5)
        gs = analytics_r.json()
        total_collected = gs.get("analytics/totalCollected", 0)
        total_paid_out  = gs.get("analytics/totalPaidOut", 0)
        total_profit    = gs.get("analytics/totalProfit", 0)
        bot.send_message(m.chat.id,
            f"📊 <b>Stats</b>\n\n"
            f"👥 Users: {health.get('users', 0)}\n"
            f"🏆 Winners: {health.get('winners', 0)}\n"
            f"💰 Total Collected: {total_collected} ብር\n"
            f"💸 Total Paid Out: {total_paid_out} ብር\n"
            f"📈 Total Profit: {total_profit} ብር\n"
            f"🗄️ DB Size: {health.get('db_size', '?')}")
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Stats error: {e}")

@bot.message_handler(commands=["pending"])
def show_pending(m):
    if m.chat.id != ADMIN_ID: return
    payments = fb_get("payments") or {}
    pending  = [(pid, p) for pid, p in payments.items()
                if isinstance(p, dict) and p.get("status") == "pending"]
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
    uid = parts[1]
    fb_set(f"temp/{uid}", None)
    payments = fb_get("payments") or {}
    count = 0
    for pid, pay in payments.items():
        if not isinstance(pay, dict): continue
        if str(pay.get("user_id")) == uid and pay.get("status") == "pending":
            fb_set(f"payments/{pid}/status", "cancelled")
            count += 1
    bot.send_message(m.chat.id,
        f"✅ User <code>{uid}</code> cleared!\n📋 {count} pending cancelled.")

@bot.message_handler(commands=["givebalance"])
def cmd_give_balance(m):
    if m.chat.id != ADMIN_ID: return
    parts = m.text.split()
    if len(parts) < 3:
        bot.send_message(m.chat.id, "Usage: /givebalance <uid> <amount>"); return
    try:
        uid = parts[1]; amount = int(parts[2])
        new_bal = update_balance(uid, amount, "add")
        bot.send_message(m.chat.id,
            f"✅ {amount} ብር ተሰጠ!\n👤 <code>{uid}</code>\n💰 New Balance: {new_bal} ብር")
        try:
            bot.send_message(int(uid),
                f"🎁 Admin {amount} ብር ሰጠህ!\n💼 Balance: <b>{new_bal} ብར</b>")
        except: pass
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Error: {e}")

@bot.message_handler(commands=["broadcast_all"])
def cmd_broadcast_all(m):
    if m.chat.id != ADMIN_ID: return
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Usage: /broadcast_all <message>"); return
    msg = parts[1]
    try:
        r = requests.get(f"{SERVER}/game-state", timeout=10)
        display_names = r.json().get("displayNames", {})
        sent = 0
        for uid in display_names.keys():
            if not str(uid).isdigit(): continue
            try:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("🎮 Play Now",
                       web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
                bot.send_message(int(uid), msg, reply_markup=kb)
                sent += 1
                time.sleep(0.05)
            except: pass
        bot.send_message(m.chat.id, f"✅ {sent} users ተላከ!")
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Error: {e}")


# ══════════════════════════════════════════
# TEXT HANDLER
# ══════════════════════════════════════════
ALLOWED_SMS_SENDERS = [ADMIN_ID]

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    uid  = str(m.from_user.id)
    text = m.text.strip()
    raw_state = fb_get(f"botstate_{uid}")
    state = raw_state.strip('"').strip("'") if isinstance(raw_state, str) else raw_state

    print(f"ID:{m.from_user.id} STATE:{state} TEXT:{text[:50]}")

    # Bank SMS from admin
    if m.from_user.id in ALLOWED_SMS_SENDERS and is_bank_sms(text):
        threading.Thread(target=handle_sms_from_webhook, args=(text,), daemon=True).start()
        return

    # Admin: set CBE account
    if state == "waiting_set_cbe" and m.from_user.id == ADMIN_ID:
        account = text.strip()
        if not (account.isdigit() and len(account) == 13):
            bot.send_message(m.chat.id, "❌ CBE account <b>13 digit</b> ያስፈልጋል!")
            fb_set(f"botstate_{uid}", None)
            return
        fb_set("bot/settings/cbe_account", account)
        try:
            requests.post(f"{SERVER}/save-accounts", json={"cbe": account}, timeout=5)
        except: pass
        fb_set(f"botstate_{uid}", None)
        bot.send_message(m.chat.id, f"✅ CBE Account ተቀይሯል!\n🏦 <code>{account}</code>")
        return

    # Admin: set Telebirr account
    if state == "waiting_set_telebirr" and m.from_user.id == ADMIN_ID:
        account = text.strip()
        if not (account.isdigit() and len(account) == 10):
            bot.send_message(m.chat.id, "❌ Telebirr <b>10 digit</b> ያስፈልጋል!")
            fb_set(f"botstate_{uid}", None)
            return
        fb_set("bot/settings/telebirr_account", account)
        try:
            requests.post(f"{SERVER}/save-accounts", json={"telebirr": account}, timeout=5)
        except: pass
        fb_set(f"botstate_{uid}", None)
        bot.send_message(m.chat.id, f"✅ Telebirr Account ተቀይሯል!\n📱 <code>{account}</code>")
        return

    # Withdrawal: amount
    if state == "waiting_wd_amount":
        try:
            amount = int(text)
        except ValueError:
            bot.send_message(m.chat.id, "❌ ቁጥር ብቻ ላክ! ለምሳሌ: <code>500</code>")
            return
        balance = get_balance(uid)
        if amount < MIN_WITHDRAWAL:
            bot.send_message(m.chat.id, f"❌ Minimum: <b>{MIN_WITHDRAWAL} ብር</b>")
            return
        if amount > MAX_WITHDRAWAL:
            bot.send_message(m.chat.id, f"❌ Maximum: <b>{MAX_WITHDRAWAL} ብር</b>")
            return
        if amount > balance:
            bot.send_message(m.chat.id, f"❌ Balance አናሳ!\n💰 Balance: <b>{balance} ብር</b>")
            return
        fb_set(f"botstate_{uid}", "waiting_wd_acct_num")
        fb_set(f"tempwd_{uid}_amount", amount)
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🏦 CBE",      callback_data="wdm_CBE"),
            InlineKeyboardButton("📱 Telebirr", callback_data="wdm_Telebirr"),
            InlineKeyboardButton("🏧 Awash",    callback_data="wdm_Awash"),
            InlineKeyboardButton("💳 Other",    callback_data="wdm_Other"),
        )
        bot.send_message(m.chat.id, f"🏧 <b>{amount} ብር</b>\nምን አይነት account?", reply_markup=kb)
        return

    # Withdrawal: account number
    if state == "waiting_wd_acct_num":
        account = text.strip()
        method  = fb_get(f"tempwd_{uid}_method") or "—"
        if method == "CBE" and not (account.isdigit() and len(account) == 13):
            bot.send_message(m.chat.id, "❌ CBE account number <b>13 digit</b> ያስገቡ!")
            fb_set(f"botstate_{uid}", None)
            send_menu(m.chat.id)
            return
        elif method == "Telebirr" and not (account.isdigit() and len(account) == 10):
            bot.send_message(m.chat.id, "❌ Telebirr ስልክ ቁጥር <b>10 digit</b> ያስገቡ!")
            fb_set(f"botstate_{uid}", None)
            send_menu(m.chat.id)
            return
        elif method == "Awash" and not (account.isdigit() and len(account) == 14):
            bot.send_message(m.chat.id, "❌ Awash account number <b>14 digit</b> ያስገቡ!")
            fb_set(f"botstate_{uid}", None)
            send_menu(m.chat.id)
            return

        amount  = fb_get(f"tempwd_{uid}_amount") or 0
        balance = get_balance(uid)

        pending = fb_get(f"users/{uid}/pending_withdrawal") or 0
        if pending > 0:
            bot.send_message(m.chat.id,
                f"⚠️ አስቀድሞ Pending Withdrawal አለዎት!\n💰 {pending} ብር እየተጠበቀ ነው።")
            fb_set(f"botstate_{uid}", None)
            return
        if amount > balance:
            bot.send_message(m.chat.id, f"❌ Balance አናሳ!\n💰 Balance: <b>{balance} ብር</b>")
            fb_set(f"botstate_{uid}", None)
            return

        # Deduct balance via server.js
        update_balance(uid, amount, "subtract")
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
        fb_set(f"botstate_{uid}", None)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎮 Play Game",
               web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
        kb.add(
            InlineKeyboardButton("💳 Deposit", callback_data="deposit"),
            InlineKeyboardButton("💰 Balance", callback_data="balance")
        )
        kb.add(
            InlineKeyboardButton("🏧 Withdraw", callback_data="withdraw"),
            InlineKeyboardButton("📊 History",  callback_data="history")
        )
        bot.send_message(m.chat.id,
            f"✅ <b>እየተላከ ነው!</b>\n\n"
            f"💰 {amount} ብር\n"
            f"📲 {method} — <code>{account}</code>\n\n"
            f"⏳ እስከ 5 ደቂቃ ሊቆይ ይችላል...",
            reply_markup=kb)

        name = m.from_user.username or m.from_user.first_name
        if method == "Telebirr":
            bot.send_message(ADMIN_ID, f"🤖AUTO|{account}|{amount}|{uid}", parse_mode=None)
        else:
            bot.send_message(ADMIN_ID,
                f"🏧 <b>New Withdrawal</b>\n"
                f"👤 {name} (<code>{uid}</code>)\n"
                f"💰 {amount} ብር\n"
                f"📲 {method} — <code>{account}</code>\n\n"
                f"⚠️ Admin Panel ላይ ያስተናግዱ")
        return

    # Unknown state — clear
    if state and state not in [None, ""]:
        fb_set(f"botstate_{uid}", None)

    send_menu(m.chat.id)


# ══════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    bot.answer_callback_query(c.id)
    uid  = str(c.from_user.id)
    data = c.data
    fb_set(f"users/{uid}/last_activity", datetime.now().timestamp())

    if data == "deposit":
        kb = InlineKeyboardMarkup(row_width=1)
        for a in [50, 100, 200, 500, 1000]:
            kb.add(InlineKeyboardButton(f"💳 {a} ብር", callback_data=f"pay_{a}"))
        bot.send_message(c.message.chat.id, "💳 <b>Amount ምረጥ:</b>", reply_markup=kb)

    elif data.startswith("pay_"):
        amount = int(data.split("_")[1])
        fb_set(f"temp/{uid}", {"amount": amount, "retry_count": 0})
        bot.send_message(c.message.chat.id,
            f"✅ <b>{amount} ብር Deposit</b>\n\n"
            f"🏦 CBE: <code>{get_cbe_account()}</code>\n"
            f"📱 Telebirr: <code>{get_telebirr_account()}</code>\n\n"
            f"💸 ከፍለህ → 📸 Screenshot ላክ")

    elif data == "balance":
        bal = get_balance(uid)
        pending_wd = fb_get(f"users/{uid}/pending_withdrawal") or 0
        text = f"💰 <b>Balance: {bal} ብር</b>"
        if pending_wd:
            text += f"\n⏳ Pending Withdrawal: {pending_wd} ብር"
        bot.send_message(c.message.chat.id, text)

    elif data == "withdraw":
        bal = get_balance(uid)
        if bal < MIN_WITHDRAWAL:
            bot.send_message(c.message.chat.id,
                f"❌ Balance አናሳ!\nMinimum: <b>{MIN_WITHDRAWAL} ብር</b>\nBalance: <b>{bal} ብር</b>")
            return
        fb_set(f"tempwd_{uid}", None)
        fb_set(f"botstate_{uid}", "waiting_wd_amount")
        bot.send_message(c.message.chat.id,
            f"🏧 <b>Withdrawal</b>\n"
            f"💰 Balance: <b>{bal} ብር</b>\n\n"
            f"ምን ያህል ብር?\n(Min: {MIN_WITHDRAWAL} | Max: {MAX_WITHDRAWAL})\n\nቁጥር ብቻ ላክ:")

    elif data == "history":
        payments  = fb_get("payments") or {}
        user_txns = [p for p in payments.values()
                     if isinstance(p, dict) and str(p.get("user_id")) == uid]
        if not user_txns:
            bot.send_message(c.message.chat.id, "📊 ምንም ታሪክ የለም")
            return
        user_txns.sort(key=lambda x: x.get("time", 0), reverse=True)
        icons = {"approved": "✅", "rejected": "❌", "pending": "⏳", "cancelled": "🚫"}
        lines = ["📊 <b>ግብይት ታሪክ:</b>\n"]
        for p in user_txns[:10]:
            icon = icons.get(p.get("status"), "❓")
            t = datetime.fromtimestamp(p.get("time",0)/1000).strftime("%m/%d %H:%M") if p.get("time") else "—"
            lines.append(f"{icon} {p.get('amount',0)} ብር — {t}")
        bot.send_message(c.message.chat.id, "\n".join(lines))

    elif data == "referral":
        _show_referral(c.message.chat.id, uid)

    elif data == "set_cbe":
        fb_set(f"botstate_{uid}", "waiting_set_cbe")
        bot.send_message(c.message.chat.id, "🏦 አዲስ CBE Account Number ላክ (13 digit):")

    elif data == "set_telebirr":
        fb_set(f"botstate_{uid}", "waiting_set_telebirr")
        bot.send_message(c.message.chat.id, "📱 አዲስ Telebirr ስልክ ቁጥር ላክ (10 digit):")

    elif data.startswith("wdm_"):
        method = data.replace("wdm_", "")
        fb_set(f"tempwd_{uid}_method", method)
        fb_set(f"botstate_{uid}", "waiting_wd_acct_num")
        hints = {"CBE":"13 digit account number","Telebirr":"10 digit ስልክ ቁጥር",
                 "Awash":"14 digit account number","Other":"Account number"}
        bot.send_message(c.message.chat.id,
            f"📲 <b>{method}</b>\n\n🔢 {hints.get(method,'Account number')} ላክ:")

    elif data.startswith("ap_"):
        parts  = data.split("_")
        pid = parts[1]; u_id = parts[2]; amount = int(parts[3])
        new_bal = update_balance(u_id, amount, "add")
        fb_set(f"payments/{pid}/status", "approved")
        fb_delete(f"temp/{u_id}")
        try:
            bot.edit_message_caption(chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n✅ <b>MANUALLY APPROVED</b>")
        except: pass
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🎮 Play Game",
                   web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={u_id}")))
            kb.add(
                InlineKeyboardButton("💳 Deposit", callback_data="deposit"),
                InlineKeyboardButton("💰 Balance", callback_data="balance")
            )
            kb.add(
                InlineKeyboardButton("🏧 Withdraw", callback_data="withdraw"),
                InlineKeyboardButton("📊 History",  callback_data="history")
            )
            bot.send_message(int(u_id),
                f"✅ <b>Deposit Approved!</b>\n\n"
                f"💰 {amount} ብር ታከለ!\n"
                f"💼 Balance: <b>{new_bal} ብር</b>",
                reply_markup=kb)
        except: pass

    elif data.startswith("re_"):
        parts = data.split("_")
        pid = parts[1]; u_id = parts[2]
        fb_set(f"payments/{pid}/status", "rejected")
        _t = fb_get(f"temp/{u_id}") or {}
        if not isinstance(_t, dict): _t = {}
        _t["retry_count"] = 0
        fb_set(f"temp/{u_id}", _t)
        try:
            bot.edit_message_caption(chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=c.message.caption + "\n\n❌ <b>REJECTED</b>")
        except: pass
        try:
            bot.send_message(int(u_id),
                "📸 Screenshot ጥራት የለውም\n\nግልጽ የሆነ screenshot ድጋሚ ላክ 👇")
        except: pass


# ══════════════════════════════════════════
# NOTIFICATION LISTENER (from server.js)
# ══════════════════════════════════════════
def notification_listener():
    while True:
        try:
            r = requests.get(f"{SERVER}/unread-notifications", timeout=5)
            notifs = r.json()
            for n in notifs:
                if not str(n["uid"]).isdigit(): continue
                try:
                    uid = str(n["uid"])
                    msg = n["message"]
                    # Clear pending withdrawal if approved/rejected
                    if any(kw in msg for kw in ["withdrawal", "ብር withdrawal", "ተፈቀደ", "rejected", "ተመለሰ"]):
                        fb_set(f"users/{uid}/pending_withdrawal", 0)
                    bot.send_message(int(uid), msg)
                    requests.post(f"{SERVER}/mark-notification-read",
                        json={"id": n["id"]}, timeout=5)
                except Exception as e:
                    print(f"Notify error {n['uid']}: {e}")
        except Exception as e:
            print(f"Listener error: {e}")
        time.sleep(5)

threading.Thread(target=notification_listener, daemon=True).start()


# ══════════════════════════════════════════
# TIMEOUT CHECKER
# ══════════════════════════════════════════
MATCH_TIMEOUT = 5 * 60

def timeout_checker():
    while True:
        try:
            now_ts   = datetime.now().timestamp()
            payments = fb_get("payments") or {}
            for pid, pay in list(payments.items()):
                if not isinstance(pay, dict): continue
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
                    fb_delete(f"bot/photo_pool/{ref.upper()}")
                try:
                    bot.send_message(int(uid),
                        f"⏰ <b>Deposit Cancelled!</b>\n\n💰 {amount} ብር\n\n"
                        f"⚠️ SMS 5 ደቂቃ ውስጥ አልደረሰም\n\nእንደገና deposit ሞክር 👇")
                    send_menu(int(uid))
                except: pass
                bot.send_message(ADMIN_ID,
                    f"⏰ <b>Timeout — Auto Cancelled</b>\n\n"
                    f"👤 {display} (<code>{uid}</code>)\n"
                    f"💰 {amount} ብር\n📋 REF: <code>{ref}</code>")
        except Exception as e:
            print(f"Timeout checker error: {e}")
        time.sleep(30)

threading.Thread(target=timeout_checker, daemon=True).start()


# ══════════════════════════════════════════
# DAILY REMINDER
# ══════════════════════════════════════════
def daily_reminder_loop():
    while True:
        try:
            now_ts = datetime.now().timestamp()
            users  = fb_get("users") or {}
            for uid, user in users.items():
                if not isinstance(user, dict): continue
                if not uid.isdigit(): continue
                last_act = user.get("last_activity")
                if not last_act: continue
                if (now_ts - float(last_act)) / 3600 < REMINDER_HOURS: continue
                last_reminder = user.get("last_reminder_sent")
                if last_reminder and (now_ts - float(last_reminder)) / 3600 < REMINDER_HOURS: continue
                bal = get_balance(uid)
                try:
                    msg = (
                        f"🎮 <b>Bingo Pro ይናፍቅሃል!</b>\n\n"
                        f"💰 Balance: <b>{bal} ብር</b>\n\n"
                        f"▶️ አሁን ተጫወት!"
                        if bal > 0 else
                        f"🎮 <b>Bingo Pro ይናፍቅሃል!</b>\n\n"
                        f"💳 Deposit አድርግ እና ተጫወት!\n▶️ ጠቅ አድርግ 👇"
                    )
                    kb = InlineKeyboardMarkup()
                    kb.add(InlineKeyboardButton("🎮 አሁን ተጫወት",
                           web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
                    if bal <= 0:
                        kb.add(InlineKeyboardButton("💳 Deposit", callback_data="deposit"))
                    bot.send_message(int(uid), msg, reply_markup=kb)
                    fb_set(f"users/{uid}/last_reminder_sent", now_ts)
                except Exception as e:
                    print(f"Reminder error {uid}: {e}")
        except Exception as e:
            print(f"daily_reminder_loop error: {e}")
        time.sleep(3600)

threading.Thread(target=daily_reminder_loop, daemon=True).start()


# ══════════════════════════════════════════
# DAILY REPORT
# ══════════════════════════════════════════
def daily_report_loop():
    while True:
        now      = datetime.now()
        next_run = now.replace(hour=DAILY_REPORT_HOUR, minute=DAILY_REPORT_MINUTE,
                               second=0, microsecond=0)
        if next_run <= now: next_run += timedelta(days=1)
        time.sleep((next_run - now).total_seconds())
        try:
            r  = requests.get(f"{SERVER}/health", timeout=5)
            h  = r.json()
            gs = requests.get(f"{SERVER}/game-state", timeout=5).json()
            total_collected = gs.get("analytics/totalCollected", 0)
            total_paid_out  = gs.get("analytics/totalPaidOut", 0)
            total_profit    = gs.get("analytics/totalProfit", 0)
            total_withdrawals = gs.get("analytics/totalWithdrawals", 0)
            bot.send_message(ADMIN_ID,
                f"📊 <b>Daily Report — {datetime.now().strftime('%Y-%m-%d')}</b>\n\n"
                f"👥 Users: {h.get('users', 0)}\n"
                f"🏆 Winners: {h.get('winners', 0)}\n"
                f"💰 Collected: {total_collected} ብር\n"
                f"💸 Paid Out: {total_paid_out} ብር\n"
                f"🏧 Withdrawals: {total_withdrawals} ብር\n"
                f"📈 Profit: {total_profit} ብር")
        except Exception as e:
            print(f"Daily report error: {e}")

threading.Thread(target=daily_report_loop, daemon=True).start()


# ══════════════════════════════════════════
# START POLLING
# ══════════════════════════════════════════
print("🚀 Bingo Bot starting...")
time.sleep(5)

while True:
    try:
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(3)
        print("✅ Bot polling started!")
        bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30,
            allowed_updates=["message", "callback_query"],
            restart_on_change=False,
            logger_level=None
        )
    except Exception as e:
        err = str(e)
        print(f"Bot crashed: {err}")
        if "Conflict" in err:
            try: bot.delete_webhook(drop_pending_updates=True)
            except: pass
            time.sleep(20)
        else:
            time.sleep(5)
