// Read-only evidence from a disposable Railway PostgreSQL database.
import postgres from 'postgres';
if (process.env.OUTBOX_STAGING_MOCK !== 'true') throw new Error('OUTBOX_STAGING_MOCK=true required');
if (process.env.TELEGRAM_BOT_TOKEN || process.env.TELEGRAM_CHAT_ID) throw new Error('Telegram credentials forbidden');
const url=process.env.DATABASE_URL;
if (!url) throw new Error('DATABASE_URL required');
const sql=postgres(url,{max:1,connect_timeout:5});
try {
 const [{db}]=await sql`SELECT current_database() AS db`;
 if (!['outbox_ci','outbox_sandbox'].includes(db)) throw new Error('Disposable database only');
 const [counts]=await sql`SELECT count(*)::integer AS total,
   count(*) FILTER (WHERE status='leased' AND lease_until<now())::integer AS expired_leases,
   count(*) FILTER (WHERE lead_id BETWEEN 99000000 AND 99199999)::integer AS reserved_test_rows
   FROM crm_telegram_outbox`;
 const states=await sql`SELECT status,count(*)::integer AS count FROM crm_telegram_outbox GROUP BY status ORDER BY status`;
 console.log('READ_ONLY_OUTBOX_EVIDENCE',JSON.stringify({db,...counts,states}));
 if (counts.reserved_test_rows!==0) throw new Error('Test rows remain in disposable database');
 console.log('PASS: independent Railway read-only test-row check');
} finally {await sql.end();}
