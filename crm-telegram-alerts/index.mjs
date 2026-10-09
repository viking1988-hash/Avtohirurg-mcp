import postgres from "postgres";
const token=process.env.TELEGRAM_BOT_TOKEN;
const chat=process.env.TELEGRAM_CHAT_ID;
const database=process.env.DATABASE_URL;
if(!token||!chat||!database){console.log(JSON.stringify({ok:false,reason:"missing_configuration"}));process.exit(0)}
const sql=postgres(database,{max:1,connect_timeout:10});
try {
 await sql`CREATE TABLE IF NOT EXISTS crm_telegram_alert_log (lead_id bigint NOT NULL, alert_type text NOT NULL, sent_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(lead_id,alert_type))`;
 const leads=await sql`SELECT id,created_at FROM inbound_leads WHERE status='new' AND created_at > now()-interval '30 days' ORDER BY created_at ASC LIMIT 200`;
 let sent=0,failed=0;
 for(const lead of leads){
  const overdue=new Date(lead.created_at).getTime()<Date.now()-30*60000;
  for(const kind of (overdue?["new","overdue"]:["new"])){
   const claimed=await sql`INSERT INTO crm_telegram_alert_log(lead_id,alert_type) SELECT ${lead.id},${kind} WHERE EXISTS(SELECT 1 FROM inbound_leads WHERE id=${lead.id} AND status='new') ON CONFLICT DO NOTHING RETURNING lead_id`;
   if(!claimed.length)continue;
   try{
    const text="Автохирург CRM: "+(kind==="new"?"🔔 Новая заявка":"⏰ Заявка без обработки более 30 минут")+" №"+lead.id;
    const response=await fetch("https://api.telegram.org/bot"+token+"/sendMessage",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({chat_id:chat,text})});
    if(!response.ok)throw Error("Telegram HTTP "+response.status);
    const body=await response.json();
    if(!body.ok)throw Error("Telegram API rejected message");
    sent++;
   }catch(error){
    failed++;
    await sql`DELETE FROM crm_telegram_alert_log WHERE lead_id=${lead.id} AND alert_type=${kind}`;
    console.error(JSON.stringify({event:"send_failed",lead_id:lead.id,kind,error:String(error)}));
   }
  }
 }
 console.log(JSON.stringify({ok:failed===0,checked:leads.length,sent,failed}));
} catch(error){console.error(JSON.stringify({ok:false,error:String(error)}));process.exitCode=1}
finally{await sql.end()}
