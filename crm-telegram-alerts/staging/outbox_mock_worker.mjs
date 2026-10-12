// Staging-only dry-run outbox worker. Never contacts Telegram or production CRM.
import postgres from 'postgres';
import { readFile } from 'node:fs/promises';

if (process.env.OUTBOX_STAGING_MOCK !== 'true') {
  throw new Error('OUTBOX_STAGING_MOCK=true is required');
}
if (process.env.TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_CHAT_ID) {
  throw new Error('Telegram credentials must not be present in mock runner');
}
const url = process.env.DATABASE_URL;
if (!url) throw new Error('DATABASE_URL is required');
const sql = postgres(url, { max: 2, connect_timeout: 5 });
const fakeLead = 99010001;
try {
  const [{ db }] = await sql`SELECT current_database() AS db`;
  if (db !== 'outbox_ci' && db !== 'outbox_sandbox') {
    throw new Error('Only disposable outbox_ci or outbox_sandbox database is permitted');
  }
  if (process.env.OUTBOX_STAGING_APPLY_SCHEMA === 'true') {
    const schema = await readFile(new URL('./001_telegram_outbox.sql', import.meta.url), 'utf8');
    await sql.unsafe(schema);
    console.log('PASS: isolated staging schema applied');
  }
  await sql.begin(async tx => {
    await tx`
      INSERT INTO crm_telegram_outbox (lead_id, alert_type)
      VALUES (${fakeLead}, 'new')
      ON CONFLICT (lead_id, alert_type) DO NOTHING
    `;
    const jobs = await tx`
      WITH candidate AS (
        SELECT id FROM crm_telegram_outbox
        WHERE lead_id = ${fakeLead} AND status = 'pending'
        FOR UPDATE SKIP LOCKED
      )
      UPDATE crm_telegram_outbox o SET
        status='leased', lease_until=now()+interval '2 minutes',
        lease_token=gen_random_uuid(), attempts=attempts+1
      FROM candidate WHERE o.id=candidate.id
      RETURNING o.id,o.lease_token
    `;
    if (jobs.length !== 1) throw new Error('Expected exactly one mock job');
    const receipt = await tx`
      UPDATE crm_telegram_outbox SET status='sent',
        sent_at=now(), telegram_message_id=99010001,
        lease_until=NULL, lease_token=NULL
      WHERE id=${jobs[0].id} AND lease_token=${jobs[0].lease_token}
        AND status='leased'
      RETURNING id
    `;
    if (receipt.length !== 1) throw new Error('Mock receipt failed');
    const verified = await tx`
      SELECT status,attempts,telegram_message_id
      FROM crm_telegram_outbox WHERE id=${jobs[0].id}
    `;
    if (verified[0].status !== 'sent' || verified[0].attempts !== 1 ||
        Number(verified[0].telegram_message_id) !== 99010001) {
      throw new Error('Mock receipt mismatch');
    }
    // Roll back the whole test: no test customer data or receipts persist.
    throw new MockRollback();
  });
} catch (error) {
  if (!(error instanceof MockRollback)) {
    console.error('FAIL: staging mock outbox worker', error.message);
    process.exitCode = 1;
  } else {
    console.log('PASS: staging mock claim, receipt, fencing and rollback');
  }
} finally {
  await sql.end();
}
function MockRollback() { this.name = 'MockRollback'; }
