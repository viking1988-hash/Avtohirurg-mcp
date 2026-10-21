import {readFile} from "node:fs/promises";
import assert from "node:assert/strict";
const source=(await readFile(new URL("./index.mjs",import.meta.url),"utf8")).replace('import postgres from "postgres";','const postgres=globalThis.__mockPostgres;');
process.env.TELEGRAM_BOT_TOKEN="FAKE_TOKEN_NEVER_USED";
process.env.TELEGRAM_CHAT_ID="FAKE_CHAT";
process.env.DATABASE_URL="FAKE_DATABASE";
async function scenario({leads,previous=[],fail=false,uncertain=false,status=403}){
 const claimed=new Set(previous),sent=[],logs=[],errors=[];
 globalThis.__mockPostgres=()=> {
  const sql=async (strings,...params)=>{
   const query=strings.join("?");
   if(query.startsWith("CREATE TABLE"))return [];
   if(query.startsWith("SELECT id,created_at"))return leads;
   if(query.startsWith("INSERT INTO")){const key=params[0]+":"+params[1];if(claimed.has(key))return [];claimed.add(key);return [{lead_id:params[0]}];}
   if(query.startsWith("DELETE FROM")){claimed.delete(params[0]+":"+params[1]);return [];}
   throw Error("Unexpected SQL: "+query);
  };
  sql.end=async()=>{};
  return sql;
 };
 const oldFetch=globalThis.fetch,oldLog=console.log,oldError=console.error,oldExitCode=process.exitCode;
 process.exitCode=0;
 globalThis.fetch=async (url,options)=>{assert.match(url,/api.telegram.org/);sent.push(JSON.parse(options.body));if(uncertain)throw Error("simulated timeout after Telegram may have accepted message");return {ok:!fail,status:fail?status:200,json:async()=>({ok:!fail})};};
 console.log=(x)=>logs.push(JSON.parse(x));console.error=(x)=>errors.push(JSON.parse(x));
 let exitCode;
 try{await import("data:text/javascript,"+encodeURIComponent(source)+"#"+Math.random());exitCode=process.exitCode;}
 finally{globalThis.fetch=oldFetch;console.log=oldLog;console.error=oldError;process.exitCode=oldExitCode;}
 return {claimed,sent,logs,errors,exitCode,summary:logs.find(x=>x.event==="alert_run_completed")};
}
const fresh=new Date().toISOString(),old=new Date(Date.now()-3600000).toISOString();
const a=await scenario({leads:[{id:1,created_at:fresh}]});
assert.equal(a.exitCode,0);assert.equal(a.summary.sent,1);assert.equal(a.summary.failed,0);assert.equal(a.sent.length,1);
const b=await scenario({leads:[{id:2,created_at:old}]});
assert.equal(b.summary.sent,2);assert.equal(b.summary.claimed,2);
const c=await scenario({leads:[{id:3,created_at:old}],previous:["3:new","3:overdue"]});
assert.equal(c.summary.sent,0);assert.equal(c.summary.already_claimed,2);
const d=await scenario({leads:[{id:4,created_at:fresh}],fail:true});
assert.equal(d.exitCode,1);assert.equal(d.summary.failed,1);assert.equal(d.claimed.size,1);assert.equal(d.errors[0].event,"send_rejected_permanent_manual_review");
assert.equal(d.errors[0].error_class,"telegram_http_403");
const forbiddenRetry=await scenario({leads:[{id:4,created_at:fresh}],previous:[...d.claimed]});
assert.equal(forbiddenRetry.sent.length,0);
const throttled=await scenario({leads:[{id:6,created_at:fresh}],fail:true,status:429});
assert.equal(throttled.claimed.size,1);
assert.equal(throttled.errors[0].event,"send_outcome_uncertain_manual_review");
assert.equal(throttled.errors[0].error_class,"telegram_http_429");
console.log("PASS: fresh, overdue, deduplication, 403 classification and 429 hold");

// A transport timeout after acceptance is ambiguous: retry eligibility may duplicate delivery.
const ambiguous=await scenario({leads:[{id:5,created_at:fresh}],uncertain:true});
assert.equal(ambiguous.exitCode,1);
assert.equal(ambiguous.sent.length,1);
assert.equal(ambiguous.claimed.size,1);
assert.equal(ambiguous.errors[0].event,"send_outcome_uncertain_manual_review");
const ambiguousRetry=await scenario({leads:[{id:5,created_at:fresh}],previous:[...ambiguous.claimed]});
assert.equal(ambiguousRetry.sent.length,0);
assert.equal(ambiguousRetry.exitCode,0);
console.log("PASS: ambiguous Telegram response retains claim, prevents automatic duplicate, requires manual review");
