/**
 * SMS Forwarder Server
 * SMS Forwarder app → POST /sms → REF+Amount ያወጣ → Bot /sms ይጠራ
 */

const https = require('https');
const http = require('http');

// ── CONFIG (environment variables) ──
const PORT     = process.env.PORT || 3001;
const BOT_URL  = process.env.BOT_URL || ''; // e.g. https://your-bot.railway.app

// ── REF extractor ──
function extractRefs(text) {
  const refs = [];
  const add = (r) => { r = r.toUpperCase(); if (!refs.includes(r)) refs.push(r); };

  for (const m of text.matchAll(/\/([A-Z0-9]{8,20})-\d+/gi))           add(m[1]);
  for (const m of text.matchAll(/\/BranchReceipt\/([A-Z0-9]{8,20})[&\-]/gi)) add(m[1]);
  for (const m of text.matchAll(/Ref\s+No\s+(FT[A-Z0-9]{6,16})/gi))    add(m[1]);
  for (const m of text.matchAll(/bank\s+transaction\s+number\s+is\s+(FT[A-Z0-9]{6,16})/gi)) add(m[1]);
  for (const m of text.matchAll(/transaction\s+number\s+is\s+([A-Z]{2}[A-Z0-9]{6,14})/gi)) add(m[1]);
  for (const m of text.matchAll(/\/receipt\/([A-Z0-9]{8,16})/gi))       add(m[1]);
  for (const m of text.matchAll(/\b(FT[A-Z0-9]{6,16})\b/gi))           add(m[1]);
  for (const m of text.matchAll(/\b(DE[A-Z0-9]{6,14})\b/gi))           add(m[1]);

  return refs;
}

// ── Amount extractor ──
function extractAmount(text) {
  const patterns = [
    /credited\s+with\s+ETB\s+([\d,]+\.?\d*)/i,
    /has\s+been\s+credited\s+with\s+ETB\s+([\d,]+\.?\d*)/i,
    /received\s+ETB\s+([\d,]+\.?\d*)/i,
    /you\s+have\s+received\s+ETB\s+([\d,]+\.?\d*)/i,
    /transferred?\s+ETB\s+([\d,]+\.?\d*)/i,
    /ETB\s+([\d,]+\.?\d*)/i,
  ];
  for (const pat of patterns) {
    const m = text.match(pat);
    if (m) {
      const val = parseFloat(m[1].replace(/,/g, '').replace(/\.$/, ''));
      if (val > 0) return val;
    }
  }
  return 0;
}

// ── Bot /sms ይጠራ ──
function forwardToBot(text) {
  if (!BOT_URL) {
    console.error('[SMS] BOT_URL not set!');
    return;
  }

  const bodyData = JSON.stringify({ text });
  const url = new URL(`${BOT_URL}/sms`);
  const isHttps = url.protocol === 'https:';
  const lib = isHttps ? https : http;

  const opts = {
    hostname: url.hostname,
    port: url.port || (isHttps ? 443 : 80),
    path: url.pathname,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(bodyData)
    }
  };

  const req = lib.request(opts, (res) => {
    let d = '';
    res.on('data', c => d += c);
    res.on('end', () => console.log(`[SMS] Bot response: ${d.slice(0, 80)}`));
  });
  req.on('error', (e) => console.error('[SMS] Bot forward error:', e.message));
  req.write(bodyData);
  req.end();
}

// ── HTTP Server ──
const server = http.createServer((req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  // Health check
  if (req.method === 'GET' && req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, msg: 'SMS Forwarder running' }));
    return;
  }

  // SMS endpoint
  if (req.method === 'POST' && req.url === '/sms') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        // JSON ወይም form data ያንብብ
        let text = '';
        try {
          const parsed = JSON.parse(body);
          text = parsed.text || parsed.sms || parsed.body || parsed.message || '';
        } catch {
          const params = new URLSearchParams(body);
          text = params.get('text') || params.get('sms') || params.get('body') || body;
        }

        text = text.trim();
        console.log(`[SMS] received: ${text.slice(0, 150)}`);

        // ወዲያውኑ response
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok' }));

        if (!text) return;

        // REF + Amount ያወጣ
        const refs = extractRefs(text);
        const amount = extractAmount(text);
        console.log(`[SMS] refs=${JSON.stringify(refs)} amount=${amount}`);

        if (refs.length === 0) {
          console.log('[SMS] No REF found — skipping');
          return;
        }

        // Bot ላይ ይላካ
        forwardToBot(text);

      } catch (e) {
        console.error('[SMS] parse error:', e.message);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok' }));
      }
    });
    return;
  }

  // 404
  res.writeHead(404);
  res.end('Not found');
});

server.listen(PORT, () => {
  console.log(`🚀 SMS Forwarder running on port ${PORT}`);
  console.log(`📡 BOT_URL: ${BOT_URL || '❌ NOT SET'}`);
});

