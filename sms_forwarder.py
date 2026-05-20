"""
SMS Forwarder Server (Python/Flask)
SMS Forwarder app → POST /sms → REF+Amount ያወጣ → Bot /sms ይጠራ
"""

import os
import re
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

PORT    = int(os.environ.get('PORT', 3001))
BOT_URL = os.environ.get('BOT_URL', '')  # e.g. https://your-bot.railway.app

# ── REF extractor ──
def extract_refs(text):
    refs = []
    def add(r):
        r = r.upper()
        if r not in refs:
            refs.append(r)

    for m in re.finditer(r'/([A-Z0-9]{8,20})-\d+', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'/BranchReceipt/([A-Z0-9]{8,20})[&\-]', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'Ref\s+No\s+(FT[A-Z0-9]{6,16})', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'bank\s+transaction\s+number\s+is\s+(FT[A-Z0-9]{6,16})', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'transaction\s+number\s+is\s+([A-Z]{2}[A-Z0-9]{6,14})', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'/receipt/([A-Z0-9]{8,16})', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'\b(FT[A-Z0-9]{6,16})\b', text, re.IGNORECASE):
        add(m.group(1))
    for m in re.finditer(r'\b(DE[A-Z0-9]{6,14})\b', text, re.IGNORECASE):
        add(m.group(1))

    return refs

# ── Amount extractor ──
def extract_amount(text):
    patterns = [
        r'credited\s+with\s+ETB\s+([\d,]+\.?\d*)',
        r'has\s+been\s+credited\s+with\s+ETB\s+([\d,]+\.?\d*)',
        r'received\s+ETB\s+([\d,]+\.?\d*)',
        r'you\s+have\s+received\s+ETB\s+([\d,]+\.?\d*)',
        r'transferred?\s+ETB\s+([\d,]+\.?\d*)',
        r'ETB\s+([\d,]+\.?\d*)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(',', '').rstrip('.'))
            if val > 0:
                return val
    return 0

# ── Bot /sms ይጠራ ──
def forward_to_bot(text):
    if not BOT_URL:
        print('[SMS] BOT_URL not set!')
        return
    try:
        url = f"{BOT_URL.rstrip('/')}/sms"
        resp = requests.post(url, json={'text': text}, timeout=10)
        print(f'[SMS] Bot response: {resp.text[:80]}')
    except Exception as e:
        print(f'[SMS] Bot forward error: {e}')

# ── Health check ──
@app.route('/', methods=['GET'])
def health():
    return jsonify({'ok': True, 'msg': 'SMS Forwarder running'})

# ── SMS endpoint ──
@app.route('/sms', methods=['POST'])
def sms():
    text = ''
    try:
        # JSON ወይም form data ያንብብ
        if request.is_json:
            data = request.get_json(force=True)
            text = data.get('text') or data.get('sms') or data.get('body') or data.get('message') or ''
        else:
            text = (request.form.get('text') or request.form.get('sms') or
                    request.form.get('body') or request.data.decode('utf-8', errors='ignore'))

        text = text.strip()
        print(f'[SMS] received: {text[:150]}')

        if not text:
            return jsonify({'status': 'ok'})

        refs   = extract_refs(text)
        amount = extract_amount(text)
        print(f'[SMS] refs={refs} amount={amount}')

        if not refs:
            print('[SMS] No REF found — skipping')
            return jsonify({'status': 'ok'})

        forward_to_bot(text)

    except Exception as e:
        print(f'[SMS] error: {e}')

    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    print(f'🚀 SMS Forwarder running on port {PORT}')
    print(f'📡 BOT_URL: {BOT_URL or "❌ NOT SET"}')
    app.run(host='0.0.0.0', port=PORT)
