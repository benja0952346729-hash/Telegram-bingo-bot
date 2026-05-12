const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } });

async function setup() {
  const client = await pool.connect();
  try {
    console.log('🔧 Setting up database...');

    await client.query(`
      CREATE TABLE IF NOT EXISTS users (
        uid TEXT PRIMARY KEY,
        display TEXT,
        balance NUMERIC DEFAULT 0,
        is_bot BOOLEAN DEFAULT false,
        pending_withdrawal NUMERIC DEFAULT 0,
        created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW()) * 1000
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS game_state (
        key TEXT PRIMARY KEY,
        value JSONB
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        amount NUMERIC,
        image TEXT,
        status TEXT DEFAULT 'pending',
        time BIGINT
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS withdrawals (
        id SERIAL PRIMARY KEY,
        uid TEXT,
        amount NUMERIC,
        account TEXT,
        status TEXT DEFAULT 'pending',
        time BIGINT
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS analytics (
        key TEXT PRIMARY KEY,
        value NUMERIC DEFAULT 0
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        uid TEXT,
        message TEXT,
        time BIGINT,
        read BOOLEAN DEFAULT false
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS promotions (
        id SERIAL PRIMARY KEY,
        text TEXT,
        photo_url TEXT,
        target_type TEXT,
        group_id TEXT,
        interval_ms BIGINT,
        next_send_at BIGINT,
        last_sent_at BIGINT,
        active BOOLEAN DEFAULT true,
        created_at BIGINT
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS all_winners (
        id SERIAL PRIMARY KEY,
        uid TEXT,
        display_name TEXT,
        card_id TEXT,
        prize NUMERIC,
        is_bot BOOLEAN,
        time BIGINT
      );
    `);

    await client.query(`
      CREATE TABLE IF NOT EXISTS admin_config (
        key TEXT PRIMARY KEY,
        value TEXT
      );
    `);

    console.log('✅ All tables created!');
  } catch(e) {
    console.error('❌ Setup error:', e.message);
  } finally {
    client.release();
    process.exit(0);
  }
}

setup();
