import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import postgres from 'postgres';

const url=process.env.TELEGRAM_TEST_DATABASE_URL;
if(!url){test('migration SQL requires disposable PostgreSQL',{skip:true},()=>{});}
else {
 const parsed=new URL(url);
 if(!['127.0.0.1','localhost'].includes(parsed.hostname)||parsed.pathname!=='/telegram_test')
   throw new Error('Refusing non-local or non-disposable database');
 test('actual staging migration is executable and enforces queue invariants',async()=>{
  const sql=postgres(url,{max:1,connect_timeout:5});
  const schema='telegram_migration_ci';
  try{
   await sql.unsafe('CREATE SCHEMA IF NOT EXISTS '+schema);
   await sql.unsafe('SET search_path TO '+schema+', public');
   const source=await readFile(new URL('./sql/001-staging-delivery-queue-migration.sql',import.meta.url),'utf8');
   const statements=source.split('\n').filter(line=>!line.trimStart().startsWith('--')).join('\n').split(';').map(x=>x.trim()).filter(Boolean);
   assert.ok(statements.length>=4,'migration should include BEGIN, CREATE TABLE, INDEX, COMMIT');
   for(const statement of statements)await sql.unsafe(statement);
   const rows=await sql.unsafe("SELECT indexname FROM pg_indexes WHERE schemaname='telegram_migration_ci' AND indexname='crm_telegram_delivery_queue_ready_idx'");
   assert.equal(rows.length,1,'partial ready index must exist');
   await sql.unsafe("INSERT INTO crm_telegram_delivery_queue(lead_id,alert_type) VALUES(900000003,'new')");
   await assert.rejects(sql.unsafe("INSERT INTO crm_telegram_delivery_queue(lead_id,alert_type) VALUES(900000003,'new')"),'unique delivery key');
   await assert.rejects(sql.unsafe("INSERT INTO crm_telegram_delivery_queue(lead_id,alert_type,state) VALUES(900000004,'new','sent')"),'sent requires receipt');
   await assert.rejects(sql.unsafe("INSERT INTO crm_telegram_delivery_queue(lead_id,alert_type,state) VALUES(900000005,'new','sending')"),'sending requires lease');
   await assert.rejects(sql.unsafe("INSERT INTO crm_telegram_delivery_queue(lead_id,alert_type,attempts) VALUES(900000006,'new',6)"),'attempt limit');
   await sql.unsafe("UPDATE crm_telegram_delivery_queue SET state='sending',attempts=1,lease_owner='test',lease_until=now()+interval '60 seconds' WHERE lead_id=900000003");
   await sql.unsafe("UPDATE crm_telegram_delivery_queue SET state='sent',telegram_message_id=900000003,sent_at=now(),lease_owner=NULL,lease_until=NULL WHERE lead_id=900000003");
   const done=await sql.unsafe("SELECT state,telegram_message_id FROM crm_telegram_delivery_queue WHERE lead_id=900000003");
   assert.equal(done[0].state,'sent');
   assert.equal(String(done[0].telegram_message_id),'900000003');
  }finally{
   await sql.unsafe('RESET search_path').catch(()=>{});
   await sql.unsafe('DROP SCHEMA IF EXISTS '+schema+' CASCADE').catch(()=>{});
   await sql.end();
  }
 });
}
