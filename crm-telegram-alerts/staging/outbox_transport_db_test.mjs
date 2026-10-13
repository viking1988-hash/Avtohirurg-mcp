// Disposable PostgreSQL integration only. All mutations roll back; no network calls.
import postgres from 'postgres';
import assert from 'node:assert/strict';
import { classifyDeliveryOutcome } from './outbox_transport_policy.mjs';

if (process.env.OUTBOX_STAGING_MOCK !== 'true') throw new Error('Mock flag required');
if (process.env.TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_CHAT_ID) throw new Error('Telegram credentials forbidden');
if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL required');
const sql = postgres(process.env.DATABASE_URL, {max:1});
class ExpectedRollback extends Error {}
try {
  const [{db}] = await sql`SELECT current_database() AS db`;
  if (!['outbox_ci','outbox_sandbox'].includes(db)) throw new Error('Disposable database only');
  for (const [i, outcome] of [
    {kind:'accepted',messageId:101},
    {kind:'http',status:429,retryAfterSeconds:20},
    {kind:'http',status:503},
    {kind:'timeout'},
    {kind:'connection_reset'},
    {kind:'crash_after_ack'},
  ].entries()) {
    try {
      await sql.begin(async tx => {
        const leadId = 99100000 + i;
        const [job] = await tx`INSERT INTO crm_telegram_outbox (lead_id,alert_type,status,attempts,lease_until,lease_token)
          VALUES (${leadId},'new','leased',1,now()+interval '2 minutes',gen_random_uuid()) RETURNING id,lease_token`;
        const result = classifyDeliveryOutcome(outcome);
        let rows;
        if (result.state === 'sent') {
          rows = await tx`UPDATE crm_telegram_outbox SET status='sent',sent_at=now(),
            telegram_message_id=${result.messageId},lease_until=NULL,lease_token=NULL
            WHERE id=${job.id} AND lease_token=${job.lease_token} AND status='leased' RETURNING *`;
        } else if (result.state === 'retryable_error') {
          rows = await tx`UPDATE crm_telegram_outbox SET status='retryable_error',
            next_attempt_at=now()+${result.retryAfterSeconds}*interval '1 second',
            last_error_code=${result.error},lease_until=NULL,lease_token=NULL
            WHERE id=${job.id} AND lease_token=${job.lease_token} AND status='leased' RETURNING *`;
        } else {
          rows = await tx`UPDATE crm_telegram_outbox SET status='review',
            last_error_code=${result.error},lease_until=NULL,lease_token=NULL
            WHERE id=${job.id} AND lease_token=${job.lease_token} AND status='leased' RETURNING *`;
        }
        assert.equal(rows.length,1);
        assert.equal(rows[0].status,result.state);
        assert.equal(rows[0].lease_token,null);
        if (result.state === 'sent') assert.equal(Number(rows[0].telegram_message_id),101);
        if (result.state === 'retryable_error') assert.ok(new Date(rows[0].next_attempt_at)>new Date());
        if (result.state === 'review') {
          const claims=await tx`UPDATE crm_telegram_outbox SET status='leased',
            lease_token=gen_random_uuid(),lease_until=now()+interval '2 minutes'
            WHERE id=${job.id} AND status IN ('pending','retryable_error') RETURNING id`;
          assert.equal(claims.length,0,'uncertain delivery must not be claimed again');
        }
        throw new ExpectedRollback();
      });
      throw new Error('Transaction unexpectedly committed');
    } catch (err) {
      if (!(err instanceof ExpectedRollback)) throw err;
    }
  }
  const [{count}] = await sql`SELECT count(*)::integer AS count FROM crm_telegram_outbox WHERE lead_id BETWEEN 99100000 AND 99100005`;
  assert.equal(count,0);
  console.log('PASS: six mock transport outcomes, PostgreSQL transitions, no blind retry, full rollback');
} finally { await sql.end(); }
