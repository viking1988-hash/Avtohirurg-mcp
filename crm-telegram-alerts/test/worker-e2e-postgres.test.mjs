import assert from "node:assert/strict";
import { createServer } from "node:http";
import postgres from "postgres";
import { createDeliveryWorker } from "../delivery-v2.mjs";
import { runOperatorAction } from "../operator.mjs";

const database = process.env.DATABASE_URL;
assert.ok(database, "DATABASE_URL must point to the disposable CI PostgreSQL service");
const sql = postgres(database, { max: 4, connect_timeout: 10, onnotice: () => {} });
let mode = "success";
const calls = new Map();
const server = createServer(async (req, res) => {
  let body = "";
  for await (const chunk of req) body += chunk;
  const payload = JSON.parse(body);
  const leadId = Number(payload.text.match(/№(\d+)/)?.[1]);
  calls.set(leadId, (calls.get(leadId) || 0) + 1);
  if (mode === "timeout") {
    res.writeHead(200, { "content-type": "application/json" });
    res.write('{"ok":true,"result":{"message_id":999}}');
    res.socket.destroy();
    return;
  }
  if (mode === "rate-limit") {
    res.writeHead(429, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: false, error_code: 429, parameters: { retry_after: 30 } }));
    return;
  }
  await new Promise(resolve => setTimeout(resolve, mode === "slow-success" ? 80 : 0));
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify({ ok: true, result: { message_id: 7000 + leadId } }));
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const endpoint = `http://127.0.0.1:${server.address().port}`;
const worker = createDeliveryWorker({
  sql, token: "TEST_ONLY_TOKEN", chatId: "TEST_ONLY_CHAT",
  apiBaseUrl: endpoint, maxAttempts: 5,
  log: () => {}, errorLog: () => {},
});

async function addLead(id, createdAt = new Date()) {
  await sql`INSERT INTO inbound_leads(id,status,created_at) VALUES (${id},'new',${createdAt})`;
}
async function state(id, type = "new") {
  const rows = await sql`SELECT state,attempt_count,telegram_message_id,next_attempt_at,last_error_class
    FROM crm_telegram_delivery_v2 WHERE lead_id=${id} AND alert_type=${type}`;
  return rows[0];
}
try {
  await sql`CREATE TABLE IF NOT EXISTS inbound_leads(
    id bigint PRIMARY KEY, status text NOT NULL, created_at timestamptz NOT NULL
  )`;
  await sql`CREATE TABLE IF NOT EXISTS crm_telegram_alert_log(
    lead_id bigint NOT NULL, alert_type text NOT NULL, sent_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(lead_id,alert_type)
  )`;
  await sql`DELETE FROM crm_telegram_delivery_operator_audit WHERE lead_id BETWEEN 990100 AND 990199`;
  await sql`DELETE FROM crm_telegram_delivery_v2 WHERE lead_id BETWEEN 990100 AND 990199`;
  await sql`DELETE FROM crm_telegram_alert_log WHERE lead_id BETWEEN 990100 AND 990199`;
  await sql`DELETE FROM inbound_leads WHERE id BETWEEN 990100 AND 990199`;

  // Normal end-to-end delivery and persisted Telegram message id.
  await addLead(990101);
  await worker.run();
  assert.equal((await state(990101)).state, "sent");
  assert.equal((await state(990101)).telegram_message_id, 10007101);
  assert.equal(calls.get(990101), 1);

  // Two simultaneous worker runs must produce one atomic claim and one send.
  await addLead(990102);
  mode = "slow-success";
  await Promise.all([worker.run(), worker.run()]);
  assert.equal((await state(990102)).state, "sent");
  assert.equal(calls.get(990102), 1);

  // Confirmed 429 rejection is scheduled using retry_after; no early retry.
  await addLead(990103);
  mode = "rate-limit";
  await worker.run();
  const limited = await state(990103);
  assert.equal(limited.state, "rejected");
  assert.ok(limited.next_attempt_at);
  assert.equal(calls.get(990103), 1);
  await worker.run();
  assert.equal(calls.get(990103), 1);
  await sql`UPDATE crm_telegram_delivery_v2 SET next_attempt_at=clock_timestamp()-interval '1 second'
    WHERE lead_id=990103 AND alert_type='new'`;
  mode = "success";
  await worker.run();
  assert.equal((await state(990103)).state, "sent");
  assert.equal(calls.get(990103), 2);

  // Telegram may accept then the connection closes: uncertain and never auto-replayed.
  await addLead(990104);
  mode = "timeout";
  await worker.run();
  assert.equal((await state(990104)).state, "uncertain");
  await worker.run();
  assert.equal(calls.get(990104), 1);

  // Operator can explicitly confirm or authorize a resend; resend decision is audited.
  const operatorArgs = {
    "lead-id": "990104", "alert-type": "new", operator: "ci-operator",
    reason: "Synthetic E2E explicit duplicate-risk decision",
  };
  const missingAck = await runOperatorAction(sql, "authorize-resend", operatorArgs)
    .then(() => false, () => true);
  assert.equal(missingAck, true);
  await runOperatorAction(sql, "authorize-resend", { ...operatorArgs, "accept-duplicate-risk": "YES" });
  mode = "success";
  await worker.run();
  assert.equal((await state(990104)).state, "sent");
  assert.equal(calls.get(990104), 2);
  const audit = await sql`SELECT action,duplicate_risk_accepted FROM crm_telegram_delivery_operator_audit
    WHERE lead_id=990104 ORDER BY audit_id DESC LIMIT 1`;
  assert.equal(audit[0].action, "authorized_resend");
  assert.equal(audit[0].duplicate_risk_accepted, true);

  // Process crash before send and expired lease become uncertain, never pending.
  await addLead(990105);
  const claimed = await worker.claimOne();
  assert.equal(claimed.lead_id, 990105);
  await sql`UPDATE crm_telegram_delivery_v2 SET claimed_at=clock_timestamp()-interval '20 minutes'
    WHERE lead_id=990105`;
  await worker.run();
  assert.equal((await state(990105)).state, "uncertain");
  assert.equal(calls.get(990105) || 0, 0);

  // Telegram accepted but DB confirmation fails: persisted sending is reconciled to uncertain.
  await addLead(990106);
  await sql`CREATE OR REPLACE FUNCTION fail_synthetic_sent_update() RETURNS trigger AS $$
    BEGIN
      IF NEW.lead_id=990106 AND NEW.state='sent' THEN RAISE EXCEPTION 'synthetic db confirmation failure'; END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql`;
  await sql`CREATE TRIGGER fail_synthetic_sent_update BEFORE UPDATE ON crm_telegram_delivery_v2
    FOR EACH ROW EXECUTE FUNCTION fail_synthetic_sent_update()`;
  const dbFailure = await worker.run().then(() => false, () => true);
  assert.equal(dbFailure, true);
  assert.equal((await state(990106)).state, "sending");
  await sql`DROP TRIGGER fail_synthetic_sent_update ON crm_telegram_delivery_v2`;
  await sql`DROP FUNCTION fail_synthetic_sent_update()`;
  await sql`UPDATE crm_telegram_delivery_v2 SET claimed_at=clock_timestamp()-interval '20 minutes'
    WHERE lead_id=990106`;
  await worker.run();
  assert.equal((await state(990106)).state, "uncertain");
  assert.equal(calls.get(990106), 1);
  await worker.run();
  assert.equal(calls.get(990106), 1);

  console.log("PASS: v2 worker PostgreSQL E2E, atomic concurrency, retry_after, uncertain recovery, operator audit");
} finally {
  await sql`DROP TRIGGER IF EXISTS fail_synthetic_sent_update ON crm_telegram_delivery_v2`;
  await sql`DROP FUNCTION IF EXISTS fail_synthetic_sent_update()`;
  await sql`DELETE FROM crm_telegram_delivery_operator_audit WHERE lead_id BETWEEN 990100 AND 990199`;
  await sql`DELETE FROM crm_telegram_delivery_v2 WHERE lead_id BETWEEN 990100 AND 990199`;
  await sql`DELETE FROM crm_telegram_alert_log WHERE lead_id BETWEEN 990100 AND 990199`;
  await sql`DELETE FROM inbound_leads WHERE id BETWEEN 990100 AND 990199`;
  await sql.end();
  await new Promise(resolve => server.close(resolve));
}
