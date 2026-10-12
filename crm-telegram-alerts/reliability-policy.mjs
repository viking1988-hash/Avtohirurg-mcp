// Pure reliability policy; not imported by production index.mjs.
export const MAX_ATTEMPTS = 5;
export const SEND_TIMEOUT_MS = 10000;
export const LEASE_MS = 60000;
export function classifyDelivery({httpStatus,telegramOk,networkError}={}) {
  if (networkError) return {state:'uncertain',retry:false,reason:'network_outcome_unknown'};
  if (httpStatus === 429) return {state:'retryable',retry:true,reason:'rate_limited'};
  if (httpStatus >= 500) return {state:'uncertain',retry:false,reason:'server_outcome_unknown'};
  if (httpStatus >= 400) return {state:'rejected',retry:false,reason:'telegram_rejected'};
  if (httpStatus === 200 && telegramOk === true) return {state:'sent',retry:false,reason:'confirmed'};
  return {state:'uncertain',retry:false,reason:'invalid_response'};
}
export function nextAction({state,attempts=0,leaseUntil=null,now=Date.now()}) {
  if (state==='sent') return 'skip';
  if (state==='uncertain') return 'manual_reconcile';
  if (state==='sending' && leaseUntil!==null && new Date(leaseUntil).getTime()>now) return 'wait';
  if (state==='sending') return 'manual_reconcile';
  if (attempts>=MAX_ATTEMPTS) return 'manual_reconcile';
  if (state==='pending'||state==='retryable') return 'claim';
  return 'manual_reconcile';
}
export function alertKinds(createdAt,now=Date.now()) {
  const time=new Date(createdAt).getTime();
  if(!Number.isFinite(time)) throw Error('invalid createdAt');
  return now-time>=30*60*1000?['new','overdue']:['new'];
}
export function validateConfig({token,chat,database}) {
  if(!token||!chat||!database) throw Error('missing_configuration');
  return true;
}
