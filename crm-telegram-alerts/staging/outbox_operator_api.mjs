// Isolated operator API. Never expose this server to public networks.
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import postgres from 'postgres';

if (process.env.OUTBOX_OPERATOR_API_ENABLED !== 'true' ||
    process.env.OUTBOX_STAGING_MOCK !== 'true') {
  throw new Error('Operator API requires explicit staging-only opt-in');
}
if (process.env.TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_CHAT_ID) {
  throw new Error('Telegram credentials forbidden');
}
const token = process.env.OUTBOX_OPERATOR_TOKEN;
if (!token || Buffer.byteLength(token) < 32) throw new Error('Strong operator token required');
const url = process.env.DATABASE_URL;
if (!url) throw new Error('Disposable DATABASE_URL required');
const host = '127.0.0.1';
const port = Number(process.env.OUTBOX_OPERATOR_PORT || 8787);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid port');
const db = postgres(url, {max:2,connect_timeout:5});
const [{name}] = await db`SELECT current_database() AS name`;
if (!['outbox_ci','outbox_sandbox'].includes(name)) {
  await db.end();
  throw new Error('Refusing non-disposable database');
}
const json = (res,status,payload) => {
  res.writeHead(status,{'content-type':'application/json','cache-control':'no-store',
    'x-content-type-options':'nosniff'});
  res.end(JSON.stringify(payload));
};
const authorized = req => {
  const raw = req.headers.authorization;
  if (typeof raw !== 'string' || !raw.startsWith('Bearer ')) return false;
  const provided = Buffer.from(raw.slice(7));
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided,expected);
};
const server = http.createServer(async (req,res) => {
  if (!authorized(req)) return json(res,401,{error:'unauthorized'});
  try {
    if (req.method==='GET' && req.url==='/review') {
      const rows = await db`SELECT id,status,attempts,last_error_code,created_at
        FROM crm_telegram_outbox WHERE status='review' ORDER BY created_at,id LIMIT 100`;
      return json(res,200,{jobs:rows});
    }
    if (req.method==='POST' && req.url==='/review/decision') {
      if (req.headers['content-type']?.split(';')[0] !== 'application/json') {
        return json(res,415,{error:'json_required'});
      }
      let body='';
      for await (const chunk of req) {
        body += chunk;
        if (Buffer.byteLength(body)>4096) return json(res,413,{error:'too_large'});
      }
      let input;
      try { input=JSON.parse(body); } catch { return json(res,400,{error:'invalid_json'}); }
      const {id,action,actor,reason,receiptMessageId=null}=input ?? {};
      if (!Number.isSafeInteger(id) || id<=0 ||
          !['mark_sent','authorize_retry'].includes(action) ||
          typeof actor!=='string' || typeof reason!=='string' ||
          (receiptMessageId!==null && (!Number.isSafeInteger(receiptMessageId)||receiptMessageId<=0))) {
        return json(res,400,{error:'invalid_input'});
      }
      await db`SELECT crm_telegram_outbox_review_decide(
        ${id},${action},${actor},${reason},${receiptMessageId})`;
      return json(res,200,{ok:true,id});
    }
    return json(res,404,{error:'not_found'});
  } catch (error) {
    // Never leak SQL, customer data or database details to API callers.
    console.error('Operator request rejected:',error.code || 'validation');
    return json(res,409,{error:'decision_rejected'});
  }
});
server.listen(port,host,()=>console.log('Operator API listening on loopback only'));
for(const signal of ['SIGTERM','SIGINT']) process.on(signal,()=>{
  server.close(()=>db.end().finally(()=>process.exit(0)));
});
