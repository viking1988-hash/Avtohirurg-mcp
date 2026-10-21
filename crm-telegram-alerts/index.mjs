import postgres from "postgres";
const token=process.env.TELEGRAM_BOT_TOKEN;
const chat=process.env.TELEGRAM_CHAT_ID;
const database=process.env.DATABASE_URL;
if(!token||!chat||!database){console.error(JSON.stringify({event:"alert_run_failed",ok:false,reason:"missing_configuration"}));process.exit(1)}
const sql=postgres(database,{max:1,connect_timeout:10,onnotice:()=>{}});
const runStarted=Date.now();
console.log(JSON.stringify({event:"alert_run_started",at:new Date().toISOString()}));
try {
 await sql`CREATE TABLE IF NOT EXISTS crm_telegram_alert_log (lead_id bigint NOT NULL, alert_type text NOT NULL, sent_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(lead_id,alert_type))`;
 const leads=await sql`SELECT id,created_at FROM inbound_leads WHERE status='new' AND created_at > now()-interval '30 days' ORDER BY created_at ASC LIMIT 200`;
 let sent=0,failed=0,claimedCount=0,alreadyClaimed=0;
 for(const lead of leads){
  const overdue=new Date(lead.created_at).getTime()<Date.now()-30*60000;
  for(const kind of (overdue?["new","overdue"]:["new"])){
   const claimed=await sql`INSERT INTO crm_telegram_alert_log(lead_id,alert_type) SELECT ${lead.id},${kind} WHERE EXISTS(SELECT 1 FROM inbound_leads WHERE id=${lead.id} AND status='new') ON CONFLICT DO NOTHING RETURNING lead_id`;
   if(!claimed.length){alreadyClaimed++;continue;}claimedCount++;
   try{
    const text="Автохирург CRM: "+(kind==="new"?"🔔 Новая заявка":"⏰ Заявка без обработки более 30 минут")+" №"+lead.id;
    const response=await fetch("https://api.telegram.org/bot"+token+"/sendMessage",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({chat_id:chat,text})});
    if(!response.ok){const err=Error("Telegram HTTP "+response.status);err.httpStatus=response.status;err.definiteRejection=response.status>=400&&response.status<500&&response.status!==429;err.permanentRejection=response.status===403;throw err;}
    const body=await response.json();
    if(!body.ok){const err=Error("Telegram API rejected message");err.definiteRejection=true;throw err;}
    sent++;
   }catch(error){
    failed++;
    if(error.definiteRejection&&!error.permanentRejection){
     await sql`DELETE FROM crm_telegram_alert_log WHERE lead_id=${lead.id} AND alert_type=${kind}`;
     console.error(JSON.stringify({event:"send_rejected_retry_eligible",lead_id:lead.id,kind,error_class:error.httpStatus?("telegram_http_"+error.httpStatus):error.definiteRejection?"telegram_rejected":"delivery_or_database_error"}));
    }else if(error.permanentRejection){
     // A permanent rejection must never be retried by the next cron run.
     // Retain the legacy claim until the durable v2 operator workflow is wired.
     console.error(JSON.stringify({event:"send_rejected_permanent_manual_review",lead_id:lead.id,kind,error_class:"telegram_http_403"}));
    }else{
     // Telegram may have accepted the message before a timeout or connection reset.
     // Retain the claim and require operator review rather than risk duplicate delivery.
     console.error(JSON.stringify({event:"send_outcome_uncertain_manual_review",lead_id:lead.id,kind,error_class:error.httpStatus?("telegram_http_"+error.httpStatus):"transport_or_database_error"}));
    }
   }
  }
 }
 console.log(JSON.stringify({event:"alert_run_completed",ok:failed===0,checked:leads.length,claimed:claimedCount,already_claimed:alreadyClaimed,sent,failed,duration_ms:Date.now()-runStarted}));
 if(failed>0)process.exitCode=1;
} catch(error){console.error(JSON.stringify({event:"alert_run_failed",ok:false,error_class:error.httpStatus?("telegram_http_"+error.httpStatus):"transport_or_database_error",duration_ms:Date.now()-runStarted}));process.exitCode=1}
finally{await sql.end()}
