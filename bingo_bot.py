"""
╔══════════════════════════════════════════════════════════════════╗
║         BINGO PRO — TELEGRAM BOT (SERVER.JS COMPATIBLE)         ║
║  Backend: PostgreSQL via server.js REST API                      ║
║  NO FIREBASE — uses /db-get /db-set /db-push only               ║
║  FIXED: photo OCR message + state race condition                 ║
║  FIXED: Amharic button labels + withdraw indentation bug         ║
║  FIXED: SMS amount used as source of truth + timeout 20min      ║
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
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
ADMIN_ID   = 6883208728
WEBAPP_URL = "https://game-production-7f86.up.railway.app"
SERVER     = os.environ.get("SERVER_URL", "https://admin-panel-production-b31a.up.railway.app")

MIN_WITHDRAWAL = 50

REFERRAL_SMALL_COUNT = 20
REFERRAL_SMALL_AMT   = 100
REFERRAL_BIG_COUNT   = 100
REFERRAL_BIG_AMT     = 5000

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

DAILY_REPORT_HOUR   = 20
DAILY_REPORT_MINUTE = 0
REMINDER_HOURS      = 24

# ══════════════════════════════════════════
# IN-MEMORY STATE CACHE (fixes race condition)
# ══════════════════════════════════════════
_state_cache = {}
_state_lock  = threading.Lock()

def _cache_key(path):
    return path

def cache_set(path, value):
    with _state_lock:
        _state_cache[_cache_key(path)] = value

def cache_get(path):
    with _state_lock:
        return _state_cache.get(_cache_key(path))

def cache_del(path):
    with _state_lock:
        _state_cache.pop(_cache_key(path), None)

# ══════════════════════════════════════════
# SERVER.JS API HELPERS  (NO FIREBASE)
# ══════════════════════════════════════════
def db_get(path):
    try:
        r = requests.get(f"{SERVER}/db-get", params={"path": path}, timeout=5)
        return r.json()
    except:
        return None

def db_set(path, value):
    try:
        requests.post(f"{SERVER}/db-set",
            json={"path": path, "value": value}, timeout=5)
        if value is None:
            cache_del(path)
        else:
            cache_set(path, value)
    except:
        pass

def db_delete(path):
    db_set(path, None)

def db_push(path, value):
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

# ── state helpers ──
def get_state(path):
    cached = cache_get(path)
    if cached is not None:
        return cached
    val = db_get(path)
    if val is not None:
        cache_set(path, val)
    return val

def set_state(path, value):
    if value is None:
        cache_del(path)
    else:
        cache_set(path, value)
    db_set(path, value)

# ── balance helpers ──
def get_balance(uid):
    try:
        r = requests.get(f"{SERVER}/get-balance", params={"uid": uid}, timeout=5)
        return int(float(r.json().get("balance", 0) or 0))
    except:
        return 0

def update_balance(uid, amount, typ="add"):
    try:
        r = requests.post(f"{SERVER}/update-balance",
            json={"uid": uid, "amount": amount, "type": typ}, timeout=5)
        return int(float(r.json().get("balance", 0) or 0))
    except:
        return 0

def ensure_user(uid, display):
    try:
        r = requests.get(f"{SERVER}/user-state",
            params={"userId": uid, "firstName": display}, timeout=5)
        data = r.json()
        return data.get("isNew", False), int(float(data.get("balance", 0) or 0))
    except:
        return False, 0

def get_cbe_account():
    val = db_get("bot/settings/cbe_account")
    if val:
        return str(val).strip('"').strip("'")
    try:
        r = requests.get(f"{SERVER}/game-state", timeout=5)
        v = r.json().get("bot/settings/cbe_account") or ""
        return str(v).strip('"').strip("'")
    except:
        return ""

def get_telebirr_account():
    val = db_get("bot/settings/telebirr_account")
    if val:
        return str(val).strip('"').strip("'")
    try:
        r = requests.get(f"{SERVER}/game-state", timeout=5)
        v = r.json().get("bot/settings/telebirr_account") or ""
        return str(v).strip('"').strip("'")
    except:
        return ""

# ══════════════════════════════════════════
# USER BOT STATE
# ══════════════════════════════════════════
def get_botstate(uid):
    cached = cache_get(f"botstate_{uid}")
    if cached is not None:
        return str(cached).strip('"').strip("'")
    val = db_get(f"botstate_{uid}")
    if val is not None:
        s = str(val).strip('"').strip("'")
        cache_set(f"botstate_{uid}", s)
        return s
    return None

def set_botstate(uid, state):
    if state is None:
        cache_del(f"botstate_{uid}")
        db_set(f"botstate_{uid}", None)
    else:
        cache_set(f"botstate_{uid}", state)
        db_set(f"botstate_{uid}", state)

# ══════════════════════════════════════════
# TEMP DEPOSIT DATA
# ══════════════════════════════════════════
def get_temp(uid):
    cached = cache_get(f"temp_{uid}")
    if cached is not None:
        return cached
    raw = db_get(f"temp/{uid}")
    if raw is None:
        return None
    if isinstance(raw, dict):
        cache_set(f"temp_{uid}", raw)
        return raw
    try:
        amount = int(float(raw))
        t = {"amount": amount, "retry_count": 0}
        cache_set(f"temp_{uid}", t)
        return t
    except:
        return None

def set_temp(uid, value):
    if value is None:
        cache_del(f"temp_{uid}")
        db_set(f"temp/{uid}", None)
    else:
        cache_set(f"temp_{uid}", value)
        db_set(f"temp/{uid}", value)

def update_temp(uid, key, val):
    t = get_temp(uid) or {}
    if not isinstance(t, dict):
        t = {"amount": 0}
    t[key] = val
    set_temp(uid, t)

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
            sms_text = (data.get("text","") or data.get("sms","") or
                        data.get("message","") or data.get("body",""))
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
        threading.Thread(target=handle_sms, args=(sms_text,), daemon=True).start()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"SMS webhook error: {e}")
        return jsonify({"status": "ok"}), 200

# ══════════════════════════════════════════
# BROADCAST ENDPOINT
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
                    print(f"Photo URL error: {e}")
    else:
        data = flask_request.get_json() or {}
        text = data.get("text", "")

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
            kb.add(InlineKeyboardButton("🎮 Play",
                   web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
            if photo_bytes:
                bot.send_photo(int(uid), io.BytesIO(photo_bytes), caption=text, reply_markup=kb)
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
def extract_refs(text):
    """
    REF formats from real SMS:
    Telebirr:  "transaction number is DE33HPM4FF"
    Telebirr:  "by transaction number DE37HMUH8D"
    Telebirr+CBE: "telebirr transaction number is DE36I14OA4 and your bank transaction number is FT26124S18CL"
    CBE url1:  "https://Mbreciept.cbe.com.et/FT261174ZP1W-41057146"
    CBE url2:  "https://apps.cbe.com.et:100/BranchReceipt/FT261246TDJJ&41057146"
    CBE url3:  "https://apps.cbe.com.et:100/?id=FT26118W65DX41057146"
    CBE ref:   "with Ref No FT26118W65DX"
    """
    if not text: return []
    refs = []

    def add(r):
        r = r.upper()
        if r not in refs:
            refs.append(r)

    # CBE Mbreciept URL: /FT261174ZP1W-41057146  → REF=FT261174ZP1W
    for m in re.finditer(r'/([A-Z0-9]{8,20})-\d+', text, re.IGNORECASE):
        add(m.group(1))

    # CBE BranchReceipt URL: /BranchReceipt/FT261246TDJJ&41057146
    for m in re.finditer(r'/BranchReceipt/([A-Z0-9]{8,20})[&\-]', text, re.IGNORECASE):
        add(m.group(1))

    # CBE id URL: /?id=FT26118W65DX41057146  → REF=FT26118W65DX (first 14 chars)
    # CBE id URL handled by bare FT pattern below

    # CBE "with Ref No FTxxxxxxxx"
    for m in re.finditer(r'Ref\s+No\s+(FT[A-Z0-9]{6,16})', text, re.IGNORECASE):
        add(m.group(1))

    # "bank transaction number is FTxxxxxxx"
    for m in re.finditer(r'bank\s+transaction\s+number\s+is\s+(FT[A-Z0-9]{6,16})', text, re.IGNORECASE):
        add(m.group(1))

    # "transaction number is DExxxxxxx" or "by transaction number DExxxxxxx"
    for m in re.finditer(r'transaction\s+number\s+is\s+([A-Z]{2}[A-Z0-9]{6,14})', text, re.IGNORECASE):
        add(m.group(1))

    # receipt URL: /receipt/DExxxxxxx
    for m in re.finditer(r'/receipt/([A-Z0-9]{8,16})', text, re.IGNORECASE):
        add(m.group(1))

    # Bare FT... anywhere in text
    for m in re.finditer(r'\b(FT[A-Z0-9]{6,16})\b', text, re.IGNORECASE):
        add(m.group(1))

    # Bare DE... anywhere in text
    for m in re.finditer(r'\b(DE[A-Z0-9]{6,14})\b', text, re.IGNORECASE):
        add(m.group(1))

    return refs

def extract_amount(text):
    """
    SMS formats supported (from real messages):
    Telebirr:  "You have received ETB 50.00 ..."
    Telebirr:  "You have received  ETB 300.00 by transaction number ..."
    CBE:       "has been credited with ETB 1,200.00 ..."
    CBE:       "has been Credited with ETB 2,000.00 ..."
    CBE:       "credited with ETB 2."   (whole number, dot at end)
    CBE:       "received ETB 2,700.00 from account ..."
    """
    patterns = [
        r'credited\s+with\s+ETB\s+([\d,]+\.?\d*)',
        r'received\s+ETB\s+([\d,]+\.?\d*)',
        r'transferred\s+ETB\s+([\d,]+\.?\d*)',
        r'transfer(?:red)?\s+ETB\s+([\d,]+\.?\d*)',
        r'ETB\s+([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)\s*ብር',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(',', '').rstrip('.')
            try:
                val = float(raw)
                if val > 0:
                    return val
            except:
                continue
    return 0.0

def is_bank_sms(text):
    if not text: return False
    t = text.lower()
    keywords = [
        "from: 127", "from: cbe",
        "ethio telecom",
        "credited with etb",
        "has been credited",
        "you have received etb",
        "received etb",
        "transferred etb",
        "transaction number is",
        "bank transaction number",
        "branchreceipt",
        "mbreciept.cbe",
        "apps.cbe.com.et",
        "ref no ft",
        "thank you for banking with cbe",
        "thank you for using telebirr",
    ]
    if any(k in t for k in keywords): return True
    if re.search(r'\bFT[A-Z0-9]{6,16}\b', text, re.IGNORECASE): return True
    if re.search(r'\bDE[A-Z0-9]{6,14}\b', text, re.IGNORECASE): return True
    return False

def is_dup_ref(ref):
    used = db_get("bot/used_refs") or {}
    return ref.upper() in used

def save_ref(ref, uid, amount):
    db_set(f"bot/used_refs/{ref.upper()}",
           {"user_id": uid, "amount": amount, "time": datetime.now().isoformat()})

def is_dup_screenshot(file_id):
    h = hashlib.sha256(file_id.encode()).hexdigest()
    used = db_get("bot/used_hashes") or {}
    return h in used

def save_screenshot_hash(file_id, uid, amount):
    h = hashlib.sha256(file_id.encode()).hexdigest()
    db_set(f"bot/used_hashes/{h}",
           {"user_id": uid, "amount": amount, "time": datetime.now().isoformat()})

def has_pending(uid):
    payments = db_get("payments") or {}
    for p in payments.values():
        if not isinstance(p, dict): continue
        if str(p.get("user_id")) == uid and p.get("status") == "pending":
            return True
    return False

# ══════════════════════════════════════════
# SAVED ACCOUNTS HELPERS
# ══════════════════════════════════════════
def get_saved_accounts(uid):
    """User ያስቀመጣቸው accounts ይመልሳል — {method: account_number}"""
    data = db_get(f"users/{uid}/saved_accounts")
    if isinstance(data, dict):
        return data
    return {}

def save_account(uid, method, account):
    """Account ያስቀምጣል — per method"""
    db_set(f"users/{uid}/saved_accounts/{method}", account)

# ══════════════════════════════════════════
# SMS HANDLER
# ══════════════════════════════════════════
def handle_sms(sms_text):
    try:
        refs = extract_refs(sms_text)
        if not refs:
            bot.send_message(ADMIN_ID,
                f"⚠️ <b>SMS ደረሰ ግን REF አልተገኘም</b>\n\n<code>{sms_text[:200]}</code>")
            return

        amount = extract_amount(sms_text)

        payments = db_get("payments") or {}
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
            for ref in refs:
                if is_dup_ref(ref):
                    bot.send_message(ADMIN_ID, f"⚠️ Duplicate SMS REF: <code>{ref}</code>")
                    return
            for ref in refs: save_ref(ref, matched_uid, amount)
            do_approve(matched_pid, matched_uid, amount, matched_ref, sms_text)
            return

        photo_pool = {k.upper(): v for k, v in (db_get("bot/photo_pool") or {}).items()}
        matched_photo = matched_photo_ref = None
        for ref in refs:
            if ref.upper() in photo_pool:
                matched_photo = photo_pool[ref.upper()]
                matched_photo_ref = ref.upper()
                break

        if matched_photo:
            for ref in refs:
                if is_dup_ref(ref):
                    bot.send_message(ADMIN_ID, f"⚠️ Duplicate SMS REF: <code>{ref}</code>")
                    return
            for r in (matched_photo.get("all_refs") or [matched_photo_ref]):
                db_delete(f"bot/photo_pool/{r.upper()}")
            for ref in refs: save_ref(ref, matched_photo["uid"], amount)
            do_approve(matched_photo["pid"], matched_photo["uid"], amount,
                       matched_photo_ref, sms_text)
        else:
            for ref in refs:
                db_set(f"bot/sms_pool/{ref.upper()}", {
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
        print(f"handle_sms error: {e}")
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
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                        {"type": "text",
                         "text": "Extract ALL transaction reference numbers from this payment screenshot. "
                                 "Look for: FT followed by letters/numbers (CBE), DE followed by letters/numbers (Telebirr). "
                                 "Reply with ONLY the reference numbers separated by comma. Example: DE49IZZB05,FT26124HX4GY. "
                                 "If not found, reply: NONE"}
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
            extracted = extract_refs(part)
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
def do_approve(pid, uid, sms_amount, ref, sms_text=""):
    """
    sms_amount — SMS ላይ ያለው ትክክለኛ amount (source of truth)
    user requested amount ጋር ሳይወዳደር SMS amount ብቻ ይጠቀማል
    """
    try:
        sms_amount = int(sms_amount) if sms_amount else 0
        if sms_amount <= 0:
            bot.send_message(ADMIN_ID,
                f"⚠️ SMS Amount 0 ነው! Manual check:\n👤 <code>{uid}</code>\n📋 <code>{ref}</code>\n\n"
                f"SMS:\n<code>{sms_text[:200]}</code>")
            return

        # Payment record ያንብብ (display name ለadmin notification)
        pay_record = db_get(f"payments/{pid}") or {}

        # SMS amount ይጠቀማል — ትክክለኛ source of truth
        new_bal = update_balance(uid, sms_amount, "add")

        db_set(f"payments/{pid}/status",         "approved")
        db_set(f"payments/{pid}/verified",        True)
        db_set(f"payments/{pid}/ref",             ref)
        db_set(f"payments/{pid}/amount",          sms_amount)   # ← SMS amount ያስቀምጥ
        db_set(f"payments/{pid}/sms_amount",      sms_amount)
        set_temp(uid, None)

        save_ref(ref, uid, sms_amount)

        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🎮 Play",
                   web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
            kb.add(
                InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"),
                InlineKeyboardButton("💰 ቀሪ ሂሳብ",   callback_data="balance")
            )
            kb.add(
                InlineKeyboardButton("🏧 ገንዘብ አውጣ", callback_data="withdraw"),
                InlineKeyboardButton("📊 ታሪክ",       callback_data="history")
            )
            bot.send_message(int(uid),
                f"✅ <b>ገንዘብ ገብቷል!</b>\n\n"
                f"💰 {sms_amount} ብር ታከለ\n"
                f"💼 አዲስ ቀሪ ሂሳብ: <b>{new_bal} ብር</b>",
                reply_markup=kb)
        except Exception as e:
            print(f"User notify error: {e}")

        display = pay_record.get("display") or uid
        bot.send_message(ADMIN_ID,
            f"✅ <b>Auto Approved!</b>\n\n"
            f"👤 {display} (<code>{uid}</code>)\n"
            f"💰 {sms_amount} ብር\n"
            f"📋 REF: <code>{ref}</code>")

    except Exception as e:
        print(f"do_approve error: {e}")
        bot.send_message(ADMIN_ID, f"❌ Approve error: {e}\nREF: {ref}")

# ══════════════════════════════════════════
# SCREENSHOT HANDLER
# ══════════════════════════════════════════
def process_screenshot(m):
    uid  = str(m.from_user.id)
    temp = get_temp(uid)

    if not temp:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"))
        bot.send_message(m.chat.id,
            "❗ <b>መጀመሪያ ገንዘብ ማስገቢያ ምረጥ!</b>\n\n"
            "👇 ገንዘብ አስገባ ተጫን → መጠን ምረጥ → ከዚያ Screenshot ላክ",
            reply_markup=kb)
        return

    amount = int(float(temp.get("amount", 0) or 0))

    if amount <= 0:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"))
        bot.send_message(m.chat.id,
            "❗ <b>መጠን አልተመረጠም!</b>\n\nገንዘብ አስገባ ተጫን → መጠን ምረጥ",
            reply_markup=kb)
        return

    file_id = m.photo[-1].file_id if m.content_type == "photo" else m.document.file_id

    if is_dup_screenshot(file_id):
        bot.send_message(m.chat.id, "🚫 ይህ Screenshot አስቀድሞ ጥቅም ላይ ዋሏል!")
        set_temp(uid, None)
        return

    bot.send_message(m.chat.id, "🔍 Screenshot እየተነበበ ነው...")

    refs = extract_refs_from_screenshot(file_id)

    retry_count = int(temp.get("retry_count", 0))

    if not refs:
        retry_count += 1
        update_temp(uid, "retry_count", retry_count)

        if retry_count < 3:
            bot.send_message(m.chat.id,
                f"⚠️ Screenshot ጥራት የለውም — ድጋሚ ላክ ({retry_count}/3)\n\n"
                f"📸 <b>ግልጽ የሆነ screenshot ላክ</b>")
        else:
            save_screenshot_hash(file_id, uid, amount)
            result = db_push("payments", {
                "user_id":          uid,
                "display":          m.from_user.username or m.from_user.first_name or uid,
                "amount":           0,
                "requested_amount": amount,
                "file_id":          file_id,
                "ref":              "",
                "status":           "pending",
                "time":             int(datetime.now().timestamp() * 1000),
                "verified":         False,
            })
            if result:
                update_temp(uid, "pid", result.key)
                update_temp(uid, "retry_count", 0)
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
            set_temp(uid, None)
            return

    save_screenshot_hash(file_id, uid, amount)
    update_temp(uid, "retry_count", 0)

    primary_ref = refs[0]
    stored_ref = temp.get("ref", "")
    if stored_ref and stored_ref.upper() in refs:
        primary_ref = stored_ref.upper()

    result = db_push("payments", {
        "user_id":          uid,
        "display":          m.from_user.username or m.from_user.first_name or uid,
        "amount":           0,              # ← SMS ሲደርስ ይሞላል — user amount አይጠቀምም
        "requested_amount": amount,         # ← user የጠየቀው (reference ብቻ)
        "file_id":          file_id,
        "ref":              primary_ref,
        "status":           "pending",
        "time":             int(datetime.now().timestamp() * 1000),
        "verified":         False,
    })
    if not result:
        bot.send_message(m.chat.id, "❌ Error! እንደገና ሞክር")
        return

    pid = result.key
    update_temp(uid, "pid", pid)
    update_temp(uid, "ref", primary_ref)

    sms_pool = {k.upper(): v for k, v in (db_get("bot/sms_pool") or {}).items()}
    matched_sms = matched_sms_ref = None
    for ref in refs:
        if ref.upper() in sms_pool:
            matched_sms     = sms_pool[ref.upper()]
            matched_sms_ref = ref.upper()
            break
            
    if matched_sms:
        for r in (matched_sms.get("all_refs") or [matched_sms_ref]):
            db_delete(f"bot/sms_pool/{r.upper()}")
        for ref in refs: save_ref(ref, uid, matched_sms.get("amount", 0))
        do_approve(pid, uid, matched_sms.get("amount", 0),
                   matched_sms_ref, matched_sms.get("text", ""))
    else:
        for ref in refs:
            db_set(f"bot/photo_pool/{ref.upper()}", {
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
                InlineKeyboardButton("✅ ፍቀድ", callback_data=f"ap_{pid}_{uid}"),
                InlineKeyboardButton("❌ ውድቅ",  callback_data=f"re_{pid}_{uid}")
            )
            bot.send_photo(ADMIN_ID, file_id,
                caption=f"📸 <b>New Screenshot</b>\n\n"
                        f"👤 {m.from_user.username or m.from_user.first_name} (<code>{uid}</code>)\n"
                        f"📝 User ጠየቀ: <b>{amount} ብር</b>\n"
                        f"⚠️ ትክክለኛ amount SMS ሲደርስ ይወሰናል\n"
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
        already = db_get(f"users/{new_uid}/referred_by")
        if already: return
        db_set(f"users/{new_uid}/referred_by", str(referrer_uid))
        db_push(f"referrals/{referrer_uid}/list",
                {"uid": str(new_uid), "time": datetime.now().isoformat()})
        old_count = db_get(f"referrals/{referrer_uid}/count") or 0
        new_count = old_count + 1
        db_set(f"referrals/{referrer_uid}/count", new_count)
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
        db_push(f"referrals/{referrer_uid}/bonuses",
                {"amount": bonus_amount, "count": count, "time": datetime.now().isoformat()})
        bot.send_message(int(referrer_uid),
            f"🏆 <b>Referral Bonus!</b>\n\n"
            f"👥 {count} ሰው አስገባህ!\n"
            f"💰 <b>+{bonus_amount} ብር</b> ታከለ!\n"
            f"💼 አዲስ ቀሪ ሂሳብ: <b>{new_bal} ብር</b>")
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
        ref_count = db_get(f"referrals/{uid}/count") or 0
        bonuses   = db_get(f"referrals/{uid}/bonuses") or {}
        total_bonus_earned = sum(
            b.get("amount", 0) for b in bonuses.values() if isinstance(b, dict)
        )
        if ref_count < REFERRAL_SMALL_COUNT:
            progress = int((ref_count / REFERRAL_SMALL_COUNT) * 10)
        elif ref_count < REFERRAL_BIG_COUNT:
            progress = int(((ref_count - REFERRAL_SMALL_COUNT) /
                            (REFERRAL_BIG_COUNT - REFERRAL_SMALL_COUNT)) * 10)
        else:
            progress = 10
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
# MENU  — አማርኛ buttons
# ══════════════════════════════════════════
def send_menu(chat_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎮 Play",
           web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={chat_id}")))
    kb.add(
        InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"),
        InlineKeyboardButton("💰 ቀሪ ሂሳብ",   callback_data="balance")
    )
    kb.add(
        InlineKeyboardButton("🏧 ገንዘብ አውጣ", callback_data="withdraw"),
        InlineKeyboardButton("📊 ታሪክ",       callback_data="history")
    )
    kb.add(InlineKeyboardButton("👥 ወዳጅ ጋብዝ", callback_data="referral"))
    bot.send_message(chat_id,
        "🎮 <b>Bingo Pro</b>\n\n"
        "🎁 <b>አሁን ያሉ Bonuses፡</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "👋 Welcome Bonus  → <b>+20 ብር</b>\n"
        "👥 ወዳጅ ጋብዝ      → <b>100 እስከ 5000 ብር</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "🏆 Prize Pool — <b>80% ለአሸናፊ!</b>\n\n"
        "👇 ምረጥ፡",
        reply_markup=kb)

# ══════════════════════════════════════════
# /start COMMAND
# ══════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid  = str(m.chat.id)
    args = m.text.split()
    referrer_uid = None
    display = m.from_user.first_name or m.from_user.username or uid

    if len(args) > 1 and args[1].startswith("deposit_"):
        try:
            amount = int(args[1].split("_")[1])
            set_temp(uid, {"amount": amount, "retry_count": 0})
            bot.send_message(m.chat.id,
                f"✅ <b>{amount} ብር ማስገቢያ</b>\n"
                f"🏦 CBE: <code>{get_cbe_account()}</code>\n"
                f"📱 Telebirr: <code>{get_telebirr_account()}</code>\n\n"
                f"💸 ከፍለህ → 📸 Screenshot ላክ")
        except: pass
        return

    if len(args) > 1 and args[1].startswith("withdraw"):
        bal = get_balance(uid)
        if bal < MIN_WITHDRAWAL:
            bot.send_message(m.chat.id,
                f"❌ ቀሪ ሂሳብ አናሳ!\nቢያንስ: <b>{MIN_WITHDRAWAL} ብር</b>\nቀሪ ሂሳብ: <b>{bal} ብር</b>")
            return
        set_botstate(uid, "waiting_wd_amount")
        bot.send_message(m.chat.id,
            f"🏧 <b>ገንዘብ ማውጫ</b>\n💰 ቀሪ ሂሳብ: <b>{bal} ብር</b>\n\nምን ያህል ብር? ቁጥር ላክ:")
        return

    if len(args) > 1 and args[1].startswith("ref"):
        referrer_uid = args[1][3:]

    is_new, balance = ensure_user(uid, display)

    if is_new:
        db_set(f"users/{uid}/display",   display)
        db_set(f"users/{uid}/username",  display)
        db_set(f"users/{uid}/joined_at", datetime.now().isoformat())

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
        db_set(f"users/{uid}/display",  display)
        db_set(f"users/{uid}/username", display)

    send_menu(m.chat.id)

# ══════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════
@bot.message_handler(commands=["balance"])
def cmd_balance(m):
    uid = str(m.chat.id)
    bal = get_balance(uid)
    pending_wd = db_get(f"users/{uid}/pending_withdrawal") or 0
    text = f"💰 <b>ቀሪ ሂሳብ: {bal} ብር</b>"
    if pending_wd:
        text += f"\n⏳ በመጠባበቅ ላይ ያለ ክፍያ: {pending_wd} ብር"
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
        r  = requests.get(f"{SERVER}/health", timeout=5)
        h  = r.json()
        gs = requests.get(f"{SERVER}/game-state", timeout=5).json()
        bot.send_message(m.chat.id,
            f"📊 <b>Stats</b>\n\n"
            f"👥 Users: {h.get('users', 0)}\n"
            f"🏆 Winners: {h.get('winners', 0)}\n"
            f"💰 Total Collected: {gs.get('analytics/totalCollected', 0)} ብር\n"
            f"💸 Total Paid Out: {gs.get('analytics/totalPaidOut', 0)} ብር\n"
            f"📈 Total Profit: {gs.get('analytics/totalProfit', 0)} ብር\n"
            f"🗄️ DB Size: {h.get('db_size', '?')}")
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Stats error: {e}")

@bot.message_handler(commands=["pending"])
def show_pending(m):
    if m.chat.id != ADMIN_ID: return
    payments = db_get("payments") or {}
    pending  = [(pid, p) for pid, p in payments.items()
                if isinstance(p, dict) and p.get("status") == "pending"]
    if not pending:
        bot.send_message(m.chat.id, "✅ ምንም pending የለም"); return
    lines = [f"⏳ <b>Pending ({len(pending)}):</b>\n"]
    for pid, p in pending[:10]:
        t = (datetime.fromtimestamp(p.get("time",0)/1000).strftime("%m/%d %H:%M")
             if p.get("time") else "—")
        lines.append(f"• {p.get('display','?')} — {p.get('amount',0)} ብር — {t}")
    bot.send_message(m.chat.id, "\n".join(lines))

@bot.message_handler(commands=["clearpending"])
def clear_pending(m):
    if m.chat.id != ADMIN_ID: return
    parts = m.text.split()
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Usage: /clearpending <user_id>"); return
    uid = parts[1]
    set_temp(uid, None)
    payments = db_get("payments") or {}
    count = 0
    for pid, pay in payments.items():
        if not isinstance(pay, dict): continue
        if str(pay.get("user_id")) == uid and pay.get("status") == "pending":
            db_set(f"payments/{pid}/status", "cancelled")
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
            f"✅ {amount} ብር ተሰጠ!\n👤 <code>{uid}</code>\n💰 ቀሪ ሂሳብ: {new_bal} ብር")
        try:
            bot.send_message(int(uid),
                f"🎁 Admin {amount} ብር ሰጠህ!\n💼 ቀሪ ሂሳብ: <b>{new_bal} ብር</b>")
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
                kb.add(InlineKeyboardButton("🎮 Play",
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
    uid   = str(m.from_user.id)
    text  = m.text.strip()
    state = get_botstate(uid)

    print(f"ID:{m.from_user.id} STATE:{repr(state)} TEXT:{text[:50]}")

    if m.from_user.id in ALLOWED_SMS_SENDERS and is_bank_sms(text):
        threading.Thread(target=handle_sms, args=(text,), daemon=True).start()
        return

    if state == "waiting_deposit_amount":
        try:
            amount = int(text)
        except ValueError:
            bot.send_message(m.chat.id, "❌ ቁጥር ብቻ ላክ! ለምሳሌ: <code>750</code>")
            return
        if amount < 50:
            bot.send_message(m.chat.id, "❌ ቢያንስ <b>50 ብር</b> ያስፈልጋል!")
            return
        set_botstate(uid, None)
        set_temp(uid, {"amount": amount, "retry_count": 0})
        bot.send_message(m.chat.id,
            f"✅ <b>{amount} ብር ማስገቢያ</b>\n\n"
            f"🏦 CBE: <code>{get_cbe_account()}</code>\n"
            f"📱 Telebirr: <code>{get_telebirr_account()}</code>\n\n"
            f"💸 ከፍለህ → 📸 Screenshot ላክ")
        return

    if state == "waiting_set_cbe" and m.from_user.id == ADMIN_ID:
        account = text.strip()
        if not (account.isdigit() and len(account) == 13):
            bot.send_message(m.chat.id, "❌ CBE account <b>13 digit</b> ያስፈልጋል!")
            set_botstate(uid, None)
            return
        db_set("bot/settings/cbe_account", account)
        try:
            requests.post(f"{SERVER}/save-accounts", json={"cbe": account}, timeout=5)
        except: pass
        set_botstate(uid, None)
        bot.send_message(m.chat.id, f"✅ CBE Account ተቀይሯል!\n🏦 <code>{account}</code>")
        return

    if state == "waiting_set_telebirr" and m.from_user.id == ADMIN_ID:
        account = text.strip()
        if not (account.isdigit() and len(account) == 10):
            bot.send_message(m.chat.id, "❌ Telebirr <b>10 digit</b> ያስፈልጋል!")
            set_botstate(uid, None)
            return
        db_set("bot/settings/telebirr_account", account)
        try:
            requests.post(f"{SERVER}/save-accounts", json={"telebirr": account}, timeout=5)
        except: pass
        set_botstate(uid, None)
        bot.send_message(m.chat.id, f"✅ Telebirr Account ተቀይሯል!\n📱 <code>{account}</code>")
        return

    if state == "waiting_wd_amount":
        try:
            amount = int(text)
        except ValueError:
            bot.send_message(m.chat.id, "❌ ቁጥር ብቻ ላክ! ለምሳሌ: <code>500</code>")
            return
        balance = get_balance(uid)
        if amount < MIN_WITHDRAWAL:
            bot.send_message(m.chat.id, f"❌ ቢያንስ: <b>{MIN_WITHDRAWAL} ብር</b>")
            return
        if amount > balance:
            bot.send_message(m.chat.id, f"❌ ቀሪ ሂሳብ አናሳ!\n💰 ቀሪ ሂሳብ: <b>{balance} ብር</b>")
            return
        set_botstate(uid, "waiting_wd_acct_num")
        cache_set(f"tempwd_{uid}_amount", amount)
        db_set(f"tempwd_{uid}_amount", amount)
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🏦 CBE",      callback_data="wdm_CBE"),
            InlineKeyboardButton("📱 Telebirr", callback_data="wdm_Telebirr"),
            InlineKeyboardButton("🏧 Awash",    callback_data="wdm_Awash"),
            InlineKeyboardButton("💳 ሌላ",      callback_data="wdm_Other"),
        )
        bot.send_message(m.chat.id,
            f"🏧 <b>{amount} ብር</b>\nምን አይነት account?", reply_markup=kb)
        return

    if state == "waiting_wd_acct_num":
        account = text.strip()
        method  = (cache_get(f"tempwd_{uid}_method") or
                   db_get(f"tempwd_{uid}_method") or "—")
        if isinstance(method, str):
            method = method.strip('"').strip("'")

        if method == "CBE" and not (account.isdigit() and len(account) == 13):
            bot.send_message(m.chat.id, "❌ CBE account number <b>13 digit</b> ያስገቡ!")
            set_botstate(uid, None)
            send_menu(m.chat.id)
            return
        elif method == "Telebirr" and not (account.isdigit() and len(account) == 10):
            bot.send_message(m.chat.id, "❌ Telebirr ስልክ ቁጥር <b>10 digit</b> ያስገቡ!")
            set_botstate(uid, None)
            send_menu(m.chat.id)
            return
        elif method == "Awash" and not (account.isdigit() and len(account) == 14):
            bot.send_message(m.chat.id, "❌ Awash account number <b>14 digit</b> ያስገቡ!")
            set_botstate(uid, None)
            send_menu(m.chat.id)
            return

        amount  = (cache_get(f"tempwd_{uid}_amount") or
                   db_get(f"tempwd_{uid}_amount") or 0)
        try: amount = int(float(amount))
        except: amount = 0

        balance = get_balance(uid)
        pending = db_get(f"users/{uid}/pending_withdrawal") or 0
        if pending > 0:
            bot.send_message(m.chat.id,
                f"⚠️ አስቀድሞ በመጠባበቅ ላይ ያለ ክፍያ አለዎት!\n💰 {pending} ብር እየተጠበቀ ነው።")
            set_botstate(uid, None)
            return
        if amount > balance:
            bot.send_message(m.chat.id,
                f"❌ ቀሪ ሂሳብ አናሳ!\n💰 ቀሪ ሂሳብ: <b>{balance} ብር</b>")
            set_botstate(uid, None)
            return

        update_balance(uid, amount, "subtract")
        db_set(f"users/{uid}/pending_withdrawal", amount)
        # ✅ Account ያስቀምጥ — ቀጥሎ ቶሎ ይጠቀምበታል
        save_account(uid, method, account)
        print(f"DEBUG withdraw saving: uid={uid} amount={amount} method={method} account={account}")
        result = db_push("bot/withdrawals", {
            "user_id": uid,
            "display": m.from_user.username or m.from_user.first_name or uid,
            "amount":  amount,
            "method":  method,
            "account": account,
            "status":  "pending",
            "time":    datetime.now().isoformat()
        })
        set_botstate(uid, None)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎮 Play",
               web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
        kb.add(
            InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"),
            InlineKeyboardButton("💰 ቀሪ ሂሳብ",   callback_data="balance")
        )
        kb.add(
            InlineKeyboardButton("🏧 ገንዘብ አውጣ", callback_data="withdraw"),
            InlineKeyboardButton("📊 ታሪክ",       callback_data="history")
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
                f"🏧 <b>ገንዘብ ማውጣት ጥያቄ</b>\n"
                f"👤 {name} (<code>{uid}</code>)\n"
                f"💰 {amount} ብር\n"
                f"📲 {method} — <code>{account}</code>\n\n"
                f"⚠️ Admin Panel ላይ ያስተናግዱ")
        return

    if state:
        set_botstate(uid, None)

    send_menu(m.chat.id)

# ══════════════════════════════════════════
# CALLBACK HANDLER  — ✅ FIXED indentation
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    bot.answer_callback_query(c.id)
    uid  = str(c.from_user.id)
    data = c.data

    if data == "deposit":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("💳 50 ብር",   callback_data="pay_50"),
            InlineKeyboardButton("💳 100 ብር",  callback_data="pay_100"),
            InlineKeyboardButton("💳 200 ብር",  callback_data="pay_200"),
            InlineKeyboardButton("💳 500 ብር",  callback_data="pay_500"),
            InlineKeyboardButton("💳 1000 ብር", callback_data="pay_1000"),
        )
        kb.add(InlineKeyboardButton("✏️ ሌላ መጠን ጻፍ", callback_data="pay_custom"))
        bot.send_message(c.message.chat.id,
            "💳 <b>ምን ያህል ብር ማስገባት ትፈልጋለህ?</b>\n\n"
            "👇 ምረጥ ወይም ✏️ ራስህ ጻፍ:",
            reply_markup=kb)

    elif data == "pay_custom":
        set_botstate(uid, "waiting_deposit_amount")
        bot.send_message(c.message.chat.id,
            "✏️ <b>ምን ያህል ብር ማስገባት ትፈልጋለህ?</b>\n\n"
            "ቁጥር ብቻ ላክ (ቢያንስ <b>50 ብር</b>):\n"
            "ለምሳሌ: <code>750</code>")

    elif data.startswith("pay_"):
        amount = int(data.split("_")[1])
        set_temp(uid, {"amount": amount, "retry_count": 0})
        bot.send_message(c.message.chat.id,
            f"✅ <b>{amount} ብር ማስገቢያ</b>\n\n"
            f"🏦 CBE: <code>{get_cbe_account()}</code>\n"
            f"📱 Telebirr: <code>{get_telebirr_account()}</code>\n\n"
            f"💸 ከፍለህ → 📸 Screenshot ላክ")

    elif data == "balance":
        bal = get_balance(uid)
        pending_wd = db_get(f"users/{uid}/pending_withdrawal") or 0
        text = f"💰 <b>ቀሪ ሂሳብ: {bal} ብር</b>"
        if pending_wd:
            text += f"\n⏳ በመጠባበቅ ላይ ያለ ክፍያ: {pending_wd} ብር"
        bot.send_message(c.message.chat.id, text)

    # ✅ FIXED: withdraw block now correctly indented as elif (not nested inside balance)
    elif data == "withdraw":
        try:
            wd_status = requests.get(f"{SERVER}/withdrawal-status", timeout=5).json()
            wd_enabled = wd_status.get("enabled", True)
        except:
            wd_enabled = True

        if not wd_enabled:
            bot.send_message(c.message.chat.id,
                "🌙 <b>ገንዘብ ማውጣት አሁን ዝግ ነው</b>\n\n"
                "━━━━━━━━━━━━━━━━━\n"
                "⏰ ስርዓቱ በጊዜያዊነት ተዘግቷል\n\n"
                "✅ በቅርቡ ይከፈታል — ድጋሚ ሞክር!\n"
                "━━━━━━━━━━━━━━━━━")
            return

        bal = get_balance(uid)
        if bal < MIN_WITHDRAWAL:
            bot.send_message(c.message.chat.id,
                f"❌ ቀሪ ሂሳብ አናሳ!\nቢያንስ: <b>{MIN_WITHDRAWAL} ብር</b>\nቀሪ ሂሳብ: <b>{bal} ብር</b>")
            return
        # Pending withdrawal ካለ አይፈቀድም
        pending_wd = db_get(f"users/{uid}/pending_withdrawal") or 0
        if pending_wd > 0:
            bot.send_message(c.message.chat.id,
                f"⚠️ <b>አስቀድሞ በመጠባበቅ ላይ ያለ ክፍያ አለዎት!</b>\n\n"
                f"💰 {pending_wd} ብር እየተጠበቀ ነው\n\n"
                f"Admin ከፈቀደ በኋላ እንደገና ሞክር")
            return
        set_botstate(uid, "waiting_wd_amount")
        bot.send_message(c.message.chat.id,
            f"🏧 <b>ገንዘብ ማውጣት</b>\n"
            f"💰 ቀሪ ሂሳብ: <b>{bal} ብር</b>\n\n"
            f"ምን ያህል ብር?\n(ቢያንስ: {MIN_WITHDRAWAL} ብር)\n\nቁጥር ብቻ ላክ:")

    elif data == "history":
        # ── Deposits ──
        payments  = db_get("payments") or {}
        deposits  = [p for p in payments.values()
                     if isinstance(p, dict) and str(p.get("user_id")) == uid]

        # ── Withdrawals ──
        withdrawals_all = db_get("bot/withdrawals") or {}
        withdrawals = [w for w in withdrawals_all.values()
                       if isinstance(w, dict) and str(w.get("user_id")) == uid]

        if not deposits and not withdrawals:
            bot.send_message(c.message.chat.id, "📊 ምንም ታሪክ የለም")
            return

        dep_icons = {"approved": "✅", "rejected": "❌", "pending": "⏳", "cancelled": "🚫"}
        wd_icons  = {"paid": "✅", "approved": "✅", "pending": "⏳",
                     "rejected": "❌", "cancelled": "🚫"}

        lines = ["📊 <b>ግብይት ታሪክ:</b>\n"]

        # Deposits — ቅርብ 7
        if deposits:
            deposits.sort(key=lambda x: x.get("time", 0), reverse=True)
            lines.append("💳 <b>ገንዘብ ማስገቢያ:</b>")
            for p in deposits[:7]:
                icon = dep_icons.get(p.get("status"), "❓")
                amt  = p.get("sms_amount") or p.get("requested_amount") or p.get("amount") or 0
                t    = (datetime.fromtimestamp(p.get("time", 0) / 1000).strftime("%m/%d %H:%M")
                        if p.get("time") else "—")
                lines.append(f"  {icon} {amt} ብር — {t}")

        # Withdrawals — ቅርብ 7
        if withdrawals:
            lines.append("\n🏧 <b>ገንዘብ ማውጣት:</b>")
            withdrawals.sort(
                key=lambda x: x.get("time", ""),
                reverse=True
            )
            for w in withdrawals[:7]:
                icon   = wd_icons.get(w.get("status"), "⏳")
                amt    = w.get("amount", 0)
                method = w.get("method", "")
                t      = w.get("time", "—")
                if t and t != "—":
                    try:
                        t = datetime.fromisoformat(t).strftime("%m/%d %H:%M")
                    except:
                        t = t[:16]
                lines.append(f"  {icon} {amt} ብር — {method} — {t}")

        bot.send_message(c.message.chat.id, "\n".join(lines))

    elif data == "referral":
        _show_referral(c.message.chat.id, uid)

    elif data == "set_cbe":
        set_botstate(uid, "waiting_set_cbe")
        bot.send_message(c.message.chat.id, "🏦 አዲስ CBE Account Number ላክ (13 digit):")

    elif data == "set_telebirr":
        set_botstate(uid, "waiting_set_telebirr")
        bot.send_message(c.message.chat.id, "📱 አዲስ Telebirr ስልክ ቁጥር ላክ (10 digit):")

    elif data.startswith("wdm_"):
        method = data.replace("wdm_", "")
        cache_set(f"tempwd_{uid}_method", method)
        db_set(f"tempwd_{uid}_method", method)
        set_botstate(uid, "waiting_wd_acct_num")
        hints = {"CBE":"13 digit account number","Telebirr":"10 digit ስልክ ቁጥር",
                 "Awash":"14 digit account number","Other":"Account number"}

        # Saved account ካለ button ያሳይ
        saved = get_saved_accounts(uid)
        saved_acct = saved.get(method)

        kb = InlineKeyboardMarkup()
        if saved_acct:
            kb.add(InlineKeyboardButton(
                f"✅ {saved_acct} ተጠቀም",
                callback_data=f"use_saved_{method}_{saved_acct}"
            ))
        bot.send_message(c.message.chat.id,
            f"📲 <b>{method}</b>\n\n"
            + (f"💾 የቀደመ account: <code>{saved_acct}</code>\n\n" if saved_acct else "")
            + f"🔢 {hints.get(method,'Account number')} ላክ\nወይም 👆 የቀደመውን ተጠቀም:",
            reply_markup=kb if saved_acct else None)

    elif data.startswith("use_saved_"):
        # format: use_saved_CBE_1000641057146
        parts  = data.split("_", 3)
        method = parts[2]
        account = parts[3]
        amount = (cache_get(f"tempwd_{uid}_amount") or
                  db_get(f"tempwd_{uid}_amount") or 0)
        try: amount = int(float(amount))
        except: amount = 0

        balance = get_balance(uid)
        pending = db_get(f"users/{uid}/pending_withdrawal") or 0
        if pending > 0:
            bot.send_message(c.message.chat.id,
                f"⚠️ አስቀድሞ በመጠባበቅ ላይ ያለ ክፍያ አለዎት!\n💰 {pending} ብር እየተጠበቀ ነው።")
            set_botstate(uid, None)
            return
        if amount > balance:
            bot.send_message(c.message.chat.id,
                f"❌ ቀሪ ሂሳብ አናሳ!\n💰 ቀሪ ሂሳብ: <b>{balance} ብር</b>")
            set_botstate(uid, None)
            return

        update_balance(uid, amount, "subtract")
        db_set(f"users/{uid}/pending_withdrawal", amount)
        db_push("bot/withdrawals", {
            "user_id": uid,
            "display": c.from_user.username or c.from_user.first_name or uid,
            "amount":  amount,
            "method":  method,
            "account": account,
            "status":  "pending",
            "time":    datetime.now().isoformat()
        })
        set_botstate(uid, None)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎮 Play",
               web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
        kb.add(
            InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"),
            InlineKeyboardButton("💰 ቀሪ ሂሳብ",   callback_data="balance")
        )
        kb.add(
            InlineKeyboardButton("🏧 ገንዘብ አውጣ", callback_data="withdraw"),
            InlineKeyboardButton("📊 ታሪክ",       callback_data="history")
        )
        bot.send_message(c.message.chat.id,
            f"✅ <b>እየተላከ ነው!</b>\n\n"
            f"💰 {amount} ብር\n"
            f"📲 {method} — <code>{account}</code>\n\n"
            f"⏳ እስከ 5 ደቂቃ ሊቆይ ይችላል...",
            reply_markup=kb)

        name = c.from_user.username or c.from_user.first_name
        if method == "Telebirr":
            bot.send_message(ADMIN_ID, f"🤖AUTO|{account}|{amount}|{uid}", parse_mode=None)
        else:
            bot.send_message(ADMIN_ID,
                f"🏧 <b>ገንዘብ ማውጣት ጥያቄ</b>\n"
                f"👤 {name} (<code>{uid}</code>)\n"
                f"💰 {amount} ብር\n"
                f"📲 {method} — <code>{account}</code>\n\n"
                f"⚠️ Admin Panel ላይ ያስተናግዱ")

    elif data.startswith("ap_"):
        parts = data.split("_")
        pid   = parts[1]; u_id = parts[2]

        # SMS amount ከ payment record ያንብብ
        pay_record = db_get(f"payments/{pid}") or {}
        sms_amount = int(float(pay_record.get("sms_amount", 0) or 0))
        req_amount = int(float(pay_record.get("requested_amount", 0) or
                               pay_record.get("amount", 0) or 0))

        if sms_amount <= 0:
            # SMS ገና አልደረሰም — admin manually amount ይጨምር
            bot.answer_callback_query(c.id,
                "⚠️ SMS amount ገና አልደረሰም! Amount ለማስቀመጥ /givebalance ይጠቀሙ",
                show_alert=True)
            bot.send_message(ADMIN_ID,
                f"⚠️ <b>Manual Approve ያስፈልጋል</b>\n\n"
                f"👤 <code>{u_id}</code>\n"
                f"📋 PID: <code>{pid}</code>\n"
                f"📝 User ጠየቀ: <b>{req_amount} ብር</b>\n\n"
                f"SMS amount አልደረሰም። ትክክለኛ amount ያረጋግጡ ከዚያ:\n"
                f"<code>/givebalance {u_id} [amount]</code>")
            return

        new_bal = update_balance(u_id, sms_amount, "add")
        db_set(f"payments/{pid}/status",  "approved")
        db_set(f"payments/{pid}/verified", True)
        set_temp(u_id, None)
        try:
            bot.edit_message_caption(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=(c.message.caption or "") + f"\n\n✅ <b>ጸድቋል — {sms_amount} ብር</b>")
        except: pass
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🎮 Play",
                   web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={u_id}")))
            kb.add(
                InlineKeyboardButton("💳 ገንዘብ አስገባ", callback_data="deposit"),
                InlineKeyboardButton("💰 ቀሪ ሂሳብ",   callback_data="balance")
            )
            kb.add(
                InlineKeyboardButton("🏧 ገንዘብ አውጣ", callback_data="withdraw"),
                InlineKeyboardButton("📊 ታሪክ",       callback_data="history")
            )
            bot.send_message(int(u_id),
                f"✅ <b>ገንዘብ ገብቷል!</b>\n\n"
                f"💰 {sms_amount} ብር ታከለ!\n"
                f"💼 ቀሪ ሂሳብ: <b>{new_bal} ብር</b>",
                reply_markup=kb)
        except: pass

    elif data.startswith("re_"):
        parts = data.split("_")
        pid = parts[1]; u_id = parts[2]
        db_set(f"payments/{pid}/status", "rejected")
        update_temp(u_id, "retry_count", 0)
        try:
            bot.edit_message_caption(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                caption=(c.message.caption or "") + "\n\n❌ <b>ውድቅ ሆኗል</b>")
        except: pass
        try:
            bot.send_message(int(u_id),
                "📸 Screenshot ጥራት የለውም\n\nግልጽ የሆነ screenshot ድጋሚ ላክ 👇")
        except: pass

# ══════════════════════════════════════════
# NOTIFICATION LISTENER
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
                    if any(kw in msg for kw in ["withdrawal","ብር withdrawal","ተፈቀደ","rejected","ተመለሰ"]):
                        db_set(f"users/{uid}/pending_withdrawal", 0)
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
MATCH_TIMEOUT      = 20 * 60  # 20 ደቂቃ
PHOTO_POOL_TIMEOUT = 20 * 60  # 20 ደቂቃ

_cancelled_pids = set()  # አንድ ጊዜ cancel የሆኑ PIDs — ድጋሚ አይሰሩም
_cancelled_lock = threading.Lock()

def timeout_checker():
    while True:
        try:
            now_ts = datetime.now().timestamp()

            # ── 1. Photo pool timeout ──
            photo_pool = db_get("bot/photo_pool") or {}
            seen_pids  = set()
            for ref_key, entry in list(photo_pool.items()):
                if not isinstance(entry, dict): continue
                if now_ts - entry.get("saved_at", 0) < PHOTO_POOL_TIMEOUT: continue
                pid = entry.get("pid")
                uid = str(entry.get("uid", ""))
                if not pid or not uid: continue
                with _cancelled_lock:
                    if pid in _cancelled_pids: continue
                    if pid in seen_pids: continue
                    seen_pids.add(pid)
                    _cancelled_pids.add(pid)
                for r in (entry.get("all_refs") or [ref_key]):
                    db_delete(f"bot/photo_pool/{r.upper()}")
                db_set(f"payments/{pid}/status", "cancelled")
                set_temp(uid, None)
                try:
                    bot.send_message(int(uid),
                        f"⏰ <b>ገንዘብ ማስገቢያ ተሰርዟል!</b>\n\n"
                        f"⚠️ SMS 15 ደቂቃ ውስጥ አልደረሰም\n\nእንደገና ሞክር 👇")
                    send_menu(int(uid))
                except: pass
                bot.send_message(ADMIN_ID,
                    f"⏰ <b>Photo Pool Timeout</b>\n"
                    f"👤 <code>{uid}</code> | REF: <code>{ref_key}</code>")

            # ── 2. Payment timeout ──
            payments = db_get("payments") or {}
            for pid, pay in list(payments.items()):
                if not isinstance(pay, dict): continue
                if pay.get("status") != "pending": continue
                if now_ts - pay.get("time", 0) / 1000 < MATCH_TIMEOUT: continue
                with _cancelled_lock:
                    if pid in _cancelled_pids: continue
                    _cancelled_pids.add(pid)
                uid     = str(pay.get("user_id"))
                ref     = pay.get("ref", "")
                display = pay.get("display") or uid
                db_set(f"payments/{pid}/status", "cancelled")
                set_temp(uid, None)
                if ref:
                    db_delete(f"bot/sms_pool/{ref.upper()}")
                    db_delete(f"bot/photo_pool/{ref.upper()}")
                try:
                    bot.send_message(int(uid),
                        f"⏰ <b>ገንዘብ ማስገቢያ ተሰርዟል!</b>\n\n"
                        f"⚠️ SMS 20 ደቂቃ ውስጥ አልደረሰም\n\nእንደገና ሞክር 👇")
                    send_menu(int(uid))
                except: pass
                bot.send_message(ADMIN_ID,
                    f"⏰ <b>Timeout — ተሰርዟል</b>\n"
                    f"👤 {display} (<code>{uid}</code>) | REF: <code>{ref}</code>")

        except Exception as e:
            print(f"Timeout checker error: {e}")
        time.sleep(60)  # 30→60sec: ብዙ DB calls ይቀንሳሉ

threading.Thread(target=timeout_checker, daemon=True).start()

# ══════════════════════════════════════════
# DAILY REMINDER
# ══════════════════════════════════════════
def daily_reminder_loop():
    while True:
        try:
            now_ts = datetime.now().timestamp()
            users  = db_get("users") or {}
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
                        f"💰 ቀሪ ሂሳብ: <b>{bal} ብር</b>\n\n▶️ አሁን ተጫወት!"
                        if bal > 0 else
                        f"🎮 <b>Bingo Pro ይናፍቅሃል!</b>\n\n"
                        f"💳 ገنዘብ አስገባ እና ተጫወት!\n▶️ ጠቅ አድርግ 👇"
                    )
                    kb = InlineKeyboardMarkup()
                    kb.add(InlineKeyboardButton("🎮 Play",
                           web_app=WebAppInfo(f"{WEBAPP_URL}/?uid={uid}")))
                    if bal <= 0:
                        kb.add(InlineKeyboardButton("💳 ገنዘብ አስገባ", callback_data="deposit"))
                    bot.send_message(int(uid), msg, reply_markup=kb)
                    db_set(f"users/{uid}/last_reminder_sent", now_ts)
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
            h  = requests.get(f"{SERVER}/health", timeout=5).json()
            gs = requests.get(f"{SERVER}/game-state", timeout=5).json()
            bot.send_message(ADMIN_ID,
                f"📊 <b>Daily Report — {datetime.now().strftime('%Y-%m-%d')}</b>\n\n"
                f"👥 Users: {h.get('users', 0)}\n"
                f"🏆 Winners: {h.get('winners', 0)}\n"
                f"💰 Collected: {gs.get('analytics/totalCollected', 0)} ብር\n"
                f"💸 Paid Out: {gs.get('analytics/totalPaidOut', 0)} ብር\n"
                f"🏧 Withdrawals: {gs.get('analytics/totalWithdrawals', 0)} ብር\n"
                f"📈 Profit: {gs.get('analytics/totalProfit', 0)} ብር")
        except Exception as e:
            print(f"Daily report error: {e}")

threading.Thread(target=daily_report_loop, daemon=True).start()

# ══════════════════════════════════════════
# 2-DAY CLEANUP LOOP
# payments, withdrawals, sms_pool, photo_pool — 2 ቀን ያለፋቸው ይጸዳሉ
# ══════════════════════════════════════════
CLEANUP_AGE = 2 * 24 * 60 * 60  # 2 ቀን በሰከንድ

def cleanup_loop():
    while True:
        try:
            now_ts = datetime.now().timestamp()

            # ── Payments (cancelled/rejected/approved + stale pending) ──
            payments = db_get("payments") or {}
            for pid, pay in list(payments.items()):
                if not isinstance(pay, dict): continue
                age = now_ts - pay.get("time", 0) / 1000
                if pay.get("status") == "pending":
                    # Pending deposit 1 ሰዓት ካለፈ → timeout checker ያመለጠው → አጸዳ
                    if age > 3600:
                        db_delete(f"payments/{pid}")
                elif age > CLEANUP_AGE:
                    db_delete(f"payments/{pid}")

            # ── Withdrawals — pending አይጸዳም (admin ማየት አለበት) ──
            withdrawals = db_get("bot/withdrawals") or {}
            for wid, w in list(withdrawals.items()):
                if not isinstance(w, dict): continue
                if w.get("status") == "pending": continue
                try:
                    t = w.get("time", "")
                    if not t: continue
                    ts = datetime.fromisoformat(t).timestamp()
                    if now_ts - ts > CLEANUP_AGE:
                        db_delete(f"bot/withdrawals/{wid}")
                except: continue

            # ── SMS pool (unmatched) ──
            sms_pool = db_get("bot/sms_pool") or {}
            for ref_key, entry in list(sms_pool.items()):
                if not isinstance(entry, dict): continue
                saved_at = entry.get("saved_at", 0)
                if now_ts - saved_at > CLEANUP_AGE:
                    db_delete(f"bot/sms_pool/{ref_key}")

            # ── Photo pool (unmatched) ──
            photo_pool = db_get("bot/photo_pool") or {}
            for ref_key, entry in list(photo_pool.items()):
                if not isinstance(entry, dict): continue
                saved_at = entry.get("saved_at", 0)
                if now_ts - saved_at > CLEANUP_AGE:
                    db_delete(f"bot/photo_pool/{ref_key}")

            print(f"✅ Cleanup done — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        except Exception as e:
            print(f"Cleanup error: {e}")

        time.sleep(24 * 60 * 60)  # ቀን 1 ጊዜ ይሰራል

threading.Thread(target=cleanup_loop, daemon=True).start()

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
