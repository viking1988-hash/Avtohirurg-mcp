import test from 'node:test';
import assert from 'node:assert/strict';
import {nextAction,classifyDelivery,MAX_ATTEMPTS} from './reliability-policy.mjs';
function createFakeStore(){
 const rows=new Map();
 return {rows,claim(id,now){const row=rows.get(id)??{state:'pending',attempts:0};const action=nextAction({...row,now});if(action==='claim'){rows.set(id,{state:'sending',attempts:row.attempts+1,leaseUntil:new Date(now+60000).toISOString()});return true;}return false;},finish(id,result){const row=rows.get(id);rows.set(id,{...row,state:result.state});}};
}
test('two competing workers: only one can claim',()=>{const db=createFakeStore();assert.equal(db.claim('new:42',1000),true);assert.equal(db.claim('new:42',1000),false)});
test('confirmed sent never reclaims',()=>{const db=createFakeStore();db.claim('new:42',1000);db.finish('new:42',classifyDelivery({httpStatus:200,telegramOk:true}));assert.equal(db.claim('new:42',100000),false)});
test('crash before confirmation: expired lease requires reconciliation',()=>{const db=createFakeStore();db.claim('new:42',1000);assert.equal(db.claim('new:42',62000),false);assert.equal(nextAction({...db.rows.get('new:42'),now:62000}),'manual_reconcile')});
test('ambiguous network result never auto-retries',()=>{const db=createFakeStore();db.claim('new:42',1000);db.finish('new:42',classifyDelivery({networkError:true}));assert.equal(db.claim('new:42',100000),false)});
test('429 can retry after explicit retryable transition',()=>{const db=createFakeStore();db.claim('new:42',1000);db.finish('new:42',classifyDelivery({httpStatus:429}));assert.equal(db.claim('new:42',100000),true)});
test('attempt cap prevents claim',()=>{assert.equal(nextAction({state:'retryable',attempts:MAX_ATTEMPTS}),'manual_reconcile')});
