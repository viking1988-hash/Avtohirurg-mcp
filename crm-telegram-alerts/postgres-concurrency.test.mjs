import test from 'node:test';
import assert from 'node:assert/strict';
import postgres from 'postgres';

// CI-only: a disposable PostgreSQL service; never point this at CRM staging/production.
const url=process.env.TELEGRAM_TEST_DATABASE_URL;
if(!url && process.env.CI)throw new Error('CI requires TELEGRAM_TEST_DATABASE_URL; PostgreSQL tests must not be skipped');
if(!url){test('PostgreSQL integration skipped without isolated test database',{skip:true},()=>{});}
else {
 const parsed=new URL(url);
 if(!['127.0.0.1','localhost'].includes(parsed.hostname)||parsed.pathname!=='/telegram_test')
   throw new Error('Refusing non-local or non-disposable database');
 const sql=postgres(url,{max:3,connect_timeout:5});
 const schema='telegram_ci';
 test('two real PostgreSQL connections enforce atomic claim and crash policy',async()=>{
  try {
   await sql.unsafe('CREATE SCHEMA IF NOT EXISTS '+schema);
   await sql.unsafe('DROP TABLE IF EXISTS '+schema+'.delivery');
   await sql.unsafe(`CREATE TABLE ${schema}.delivery(
     id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
     lead_id bigint NOT NULL, alert_type text NOT NULL CHECK(alert_type IN ('new','overdue')),
     state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','retryable','sent','uncertain','rejected')),
     attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
     lease_owner text, lease_until timestamptz, telegram_message_id bigint,
     sent_at timestamptz, created_at timestamptz DEFAULT now(),
     UNIQUE(lead_id,alert_type),
     CHECK(state <> 'sent' OR (telegram_message_id IS NOT NULL AND sent_at IS NOT NULL)),
     CHECK(state <> 'sending' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
   )`);
   await sql.unsafe("INSERT INTO telegram_ci.delivery(lead_id,alert_type) VALUES(900000001,'new')");
   const claim=`WITH candidate AS (
      SELECT id FROM telegram_ci.delivery WHERE state IN ('pending','retryable') AND attempts < 5
      ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
     UPDATE telegram_ci.delivery AS q SET state='sending',attempts=q.attempts+1,
       lease_owner=$1,lease_until=now()+interval '60 seconds'
     FROM candidate WHERE q.id=candidate.id RETURNING q.id`;
   // Transaction A retains row lock while transaction B tries SKIP LOCKED.
   let release;const locked=new Promise(resolve=>{release=resolve});
   let acquired;const ready=new Promise(resolve=>{acquired=resolve});
   const first=sql.begin(async tx=>{
      const rows=await tx.unsafe(claim,['worker-A']);
      assert.equal(rows.length,1);acquired();
      await locked;
   });
   try {
     await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('first_claim_timeout')),10000))]);
     const second=await Promise.race([sql.begin(tx=>tx.unsafe(claim,['worker-B'])),new Promise((_,reject)=>setTimeout(()=>reject(new Error('second_claim_timeout')),10000))]);
     assert.equal(second.length,0,'concurrent worker must not claim locked row');
   } finally { release(); await first; }
   assert.equal((await sql.unsafe(claim,['worker-B'])).length,0,'committed sending row must not be reclaimed');
   await sql.unsafe("UPDATE telegram_ci.delivery SET lease_until=now()-interval '1 second' WHERE lead_id=900000001");
   assert.equal((await sql.unsafe(claim,['worker-B'])).length,0,'expired sending lease is ambiguous');
   await sql.unsafe("UPDATE telegram_ci.delivery SET state='uncertain',lease_owner=NULL,lease_until=NULL WHERE lead_id=900000001");
   assert.equal((await sql.unsafe(claim,['worker-B'])).length,0,'uncertain result must not be retried');
   await assert.rejects(sql.unsafe("INSERT INTO telegram_ci.delivery(lead_id,alert_type) VALUES(900000001,'new')"));
   await assert.rejects(sql.unsafe("INSERT INTO telegram_ci.delivery(lead_id,alert_type,state) VALUES(900000002,'new','sent')"));
  }finally {
   await sql.unsafe('DROP SCHEMA IF EXISTS '+schema+' CASCADE');
   await sql.end();
  }
 });
}
