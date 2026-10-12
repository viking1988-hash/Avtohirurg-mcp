import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
const schema=readFileSync(new URL("./001_telegram_outbox.sql",import.meta.url),"utf8");
const claim=readFileSync(new URL("./002_outbox_claim_example.sql",import.meta.url),"utf8");
test("schema is additive and uses a unique delivery key",()=>{
 assert.match(schema,/CREATE TABLE IF NOT EXISTS crm_telegram_outbox/);
 assert.match(schema,/UNIQUE \(lead_id, alert_type\)/);
 assert.doesNotMatch(schema,/DROP TABLE|TRUNCATE|DELETE FROM/i);
});
test("leases are bounded and concurrently claimed safely",()=>{
 assert.match(claim,/FOR UPDATE SKIP LOCKED/);
 assert.match(claim,/interval '2 minutes'/);
 assert.match(claim,/attempts < 5/);
 assert.match(claim,/lease_token=gen_random_uuid\(\)/);
});
test("expired lease requires review rather than automatic resend",()=>{
 assert.match(claim,/status='review'/);
 assert.match(claim,/LEASE_EXPIRED_DELIVERY_UNCERTAIN/);
 assert.match(claim,/id=\$1 AND lease_token=\$2 AND status='leased'/);
});
