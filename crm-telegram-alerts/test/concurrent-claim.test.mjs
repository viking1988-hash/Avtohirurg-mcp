import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
const env={...process.env,PGCONNECT_TIMEOUT:"5"};
const sql=readFileSync(new URL("./delivery-v2-disposable.sql",import.meta.url),"utf8");
const schema=sql.slice(sql.indexOf("CREATE TEMP TABLE"),sql.indexOf("INSERT INTO crm_telegram_delivery_v2"));
assert.ok(schema.startsWith("CREATE TEMP TABLE"));
// Separate sessions require a persistent test-only table. This script runs solely
// against the disposable PostgreSQL service in GitHub Actions.
const ddl=schema.replace("CREATE TEMP TABLE","CREATE TABLE").replace("crm_telegram_delivery_v2","crm_telegram_delivery_concurrency_test");
function psql(statement){return new Promise((resolve,reject)=>{
 const child=spawn("psql",["-X","-v","ON_ERROR_STOP=1","-A","-t","-q","-c",statement],{env});
 let out="",err="";child.stdout.on("data",x=>out+=x);child.stderr.on("data",x=>err+=x);
 child.on("error",reject);child.on("close",code=>code===0?resolve(out.trim()):reject(Error(err||"psql failed")));
});}
try {
 await psql("DROP TABLE IF EXISTS crm_telegram_delivery_concurrency_test");
 await psql(ddl);
 await psql("INSERT INTO crm_telegram_delivery_concurrency_test (lead_id,alert_type,state) VALUES (910001,'new','pending')");
 const claim=`UPDATE crm_telegram_delivery_concurrency_test SET state='sending',attempt_count=attempt_count+1,claimed_at=now() WHERE lead_id=910001 AND alert_type='new' AND state='pending' RETURNING attempt_count;`;
 const results=await Promise.all([psql(claim),psql(claim)]);
 assert.deepEqual(results.map(x=>x.split("\\n")[0]).sort(),["","1"]);
 const state=await psql("SELECT state||':'||attempt_count FROM crm_telegram_delivery_concurrency_test WHERE lead_id=910001");
 assert.equal(state,"sending:1");
 console.log("PASS: two PostgreSQL sessions, exactly one atomic claim");
} finally {await psql("DROP TABLE IF EXISTS crm_telegram_delivery_concurrency_test");}
