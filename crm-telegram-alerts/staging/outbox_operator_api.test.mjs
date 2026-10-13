// CI-only integration test: loopback API, disposable PostgreSQL, no Telegram transport.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {setTimeout as sleep} from 'node:timers/promises';
import postgres from 'postgres';

const sql=postgres(process.env.DATABASE_URL,{max:1});
const token='ci-test-token-'.padEnd(40,'x');
const port=18791;
const url='http://127.0.0.1:'+port;
const env={...process.env,OUTBOX_OPERATOR_API_ENABLED:'true',OUTBOX_STAGING_MOCK:'true',
  OUTBOX_OPERATOR_TOKEN:token,OUTBOX_OPERATOR_PORT:String(port)};
delete env.TELEGRAM_BOT_TOKEN;
delete env.TELEGRAM_CHAT_ID;
const child=spawn(process.execPath,['crm-telegram-alerts/staging/outbox_operator_api.mjs'],
  {env,stdio:'ignore'});
const request=(path,options={})=>fetch(url+path,options);
const auth={authorization:'Bearer '+token};
try {
  let ready=false;
  for(let i=0;i<60;i++){
    if(child.exitCode!==null) throw new Error('API exited before ready');
    try { const r=await request('/review'); if(r.status===401){ready=true;break;} }
    catch {}
    await sleep(100);
  }
  assert.ok(ready,'API did not start');
  assert.equal((await request('/review')).status,401);
  assert.equal((await request('/review',{headers:{authorization:'Bearer wrong'}})).status,401);
  assert.equal((await request('/review',{headers:auth})).status,200);
  const [{count:before}]=await sql`SELECT count(*)::integer AS count FROM crm_telegram_outbox`;
  const decision=(data,headers={})=>request('/review/decision',{method:'POST',
    headers:{...auth,'content-type':'application/json',...headers},body:JSON.stringify(data)});
  assert.equal((await decision({id:1,action:'mark_sent',actor:'ci-user',
    reason:'Not enough evidence to confirm receipt'})).status,409);
  assert.equal((await decision({id:1,action:'delete',actor:'ci-user',
    reason:'Invalid action must be rejected'})).status,400);
  assert.equal((await decision({id:1,action:'authorize_retry',actor:'ci-user',
    reason:'Should reject unauthenticated attempt'},{authorization:'Bearer wrong'})).status,401);
  const [{count:after}]=await sql`SELECT count(*)::integer AS count FROM crm_telegram_outbox`;
  assert.equal(after,before,'Rejected requests modified jobs');
  console.log('PASS: API auth, invalid decision, forbidden action, no data mutation');
} finally {
  child.kill('SIGTERM');
  await sql.end();
}
