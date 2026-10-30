from threading import Lock
from prometheus_client import CollectorRegistry, Gauge, Counter, Histogram, generate_latest

NAMES=('queue_size','oldest_task','processing','failed','reconciliation_required_queue','retry_count','recovery_count','approval_count')
_registry=CollectorRegistry()
_telegram_send=Counter('telegram_send_total','Telegram send attempts',registry=_registry)
_telegram_success=Counter('telegram_send_success_total','Telegram sends accepted',registry=_registry)
_telegram_failure=Counter('telegram_send_failure_total','Telegram confirmed failures',registry=_registry)
_telegram_unknown=Counter('telegram_send_unknown_total','Telegram outcomes requiring reconciliation',registry=_registry)
_telegram_duplicates=Counter('telegram_duplicate_prevented_total','Duplicate Telegram sends prevented',registry=_registry)
_telegram_latency=Histogram('telegram_api_latency_seconds','Telegram Bot API latency',registry=_registry)
_gauges={name:Gauge(name,'Jarvis Sandbox '+name,registry=_registry) for name in NAMES}
_orchestrator_tasks=Counter('orchestrator_tasks_total','Orchestrator task completions by outcome',['outcome'],registry=_registry)
_orchestrator_pipeline=Histogram('orchestrator_pipeline_seconds','End-to-end orchestrator processing time',registry=_registry)
_transport_dispatch=Counter('transport_dispatch_total','Provider dispatch attempts',['provider'],registry=_registry)
_policy_retry=Counter('policy_retry_total','Retries approved by central policy',registry=_registry)
_policy_reconcile=Counter('policy_reconcile_total','Policy decisions requiring reconciliation',registry=_registry)
_event_bus_events=Counter('event_bus_events_total','Events published to the internal event bus',['event'],registry=_registry)
_dispatcher_failures=Counter('dispatcher_failures_total','Provider dispatch failures',['reason'],registry=_registry)
_provider_calls=Counter('provider_calls_total','Provider interface calls',['provider','method'],registry=_registry)
_outbox_events=Counter('outbox_events_total','Persisted transactional outbox events',registry=_registry)
_outbox_pending=Gauge('outbox_pending_total','Outbox events awaiting subscriber delivery',registry=_registry)
_outbox_replayed=Counter('outbox_replayed_total','Subscriber deliveries committed exactly once',registry=_registry)
_outbox_failed=Counter('outbox_failed_total','Subscriber deliveries failed or retried',registry=_registry)
_subscriber_errors=Counter('subscriber_errors_total','Subscriber invocation errors',registry=_registry)
_replay_latency=Histogram('replay_latency_seconds','Transactional outbox replay latency',registry=_registry)
_replay_queue=Gauge('replay_queue_size','Pending durable outbox subscriber deliveries',registry=_registry)
_crm_requests=Counter('crm_requests_total','CRM provider requests',registry=_registry)
_crm_success=Counter('crm_success_total','Successful CRM provider requests',registry=_registry)
_crm_failed=Counter('crm_failed_total','Definitively failed CRM provider requests',registry=_registry)
_crm_unknown=Counter('crm_unknown_total','CRM requests with unknown outcome',registry=_registry)
_crm_reconciliation=Counter('crm_reconciliation_total','CRM operations requiring reconciliation',registry=_registry)
_crm_duplicates=Counter('crm_duplicate_prevented_total','Duplicate CRM operations prevented',registry=_registry)
_crm_latency=Histogram('crm_latency_seconds','CRM provider request latency',registry=_registry)
_email_send=Counter('email_send_total','Sandbox SMTP submission attempts',registry=_registry)
_email_success=Counter('email_success_total','Emails accepted by the isolated SMTP sink',registry=_registry)
_email_failed=Counter('email_failed_total','Definitively failed email submissions',registry=_registry)
_email_unknown=Counter('email_unknown_total','Email submissions with an ambiguous SMTP result',registry=_registry)
_email_duplicates=Counter('email_duplicate_prevented_total','Duplicate email intents prevented',registry=_registry)
_email_reconciliation=Counter('email_reconciliation_total','Email operations requiring reconciliation',registry=_registry)
_email_latency=Histogram('email_latency_seconds','Sandbox SMTP submission latency',registry=_registry)
_http_requests=Counter('http_provider_requests_total','Generic HTTP provider requests',registry=_registry)
_http_success=Counter('http_provider_success_total','Successful generic HTTP provider requests',registry=_registry)
_http_failed=Counter('http_provider_failed_total','Definitively failed generic HTTP provider requests',registry=_registry)
_http_unknown=Counter('http_provider_unknown_total','Generic HTTP requests requiring reconciliation',registry=_registry)
_http_latency=Histogram('http_provider_latency_seconds','Generic HTTP provider request latency',registry=_registry)
_outbox_last={'events':0,'replayed':0,'failed':0,'errors':0}

_reconciliation_metrics={
 name:Counter(name,description,registry=_registry)
 for name,description in (
  ('reconciliation_total','Reconciliation actions requested'),
  ('reconciliation_success_total','Reconciliations completed successfully'),
  ('reconciliation_failed_total','Reconciliation actions rejected or failed'),
  ('delivery_confirmed_total','Provider deliveries confirmed by an operator'),
  ('delivery_rejected_total','Provider non-delivery confirmed by an operator'),
  ('reconciliation_required_total','Tasks moved into reconciliation required'),
  ('manual_review_total','Manual reconciliation reviews started'),
  ('provider_unknown_total','Provider outcomes classified unknown'))}
_metric_lock=Lock()
_last_persisted={name:0 for name in _reconciliation_metrics}

def observe_orchestrator(outcome,elapsed,provider):
 _orchestrator_tasks.labels(outcome=outcome).inc()
 _orchestrator_pipeline.observe(max(0,elapsed))

def transport_dispatch(provider): _transport_dispatch.labels(provider=provider).inc()
def policy_retry(): _policy_retry.inc()
def policy_reconcile(): _policy_reconcile.inc()
def provider_call(provider,method): _provider_calls.labels(provider=provider,method=method).inc()
def dispatcher_failure(reason): _dispatcher_failures.labels(reason=reason).inc()
def event_published(name): _event_bus_events.labels(event=name).inc()

def replay_latency(elapsed): _replay_latency.observe(max(0,elapsed))
def outbox_replayed(): _outbox_replayed.inc()
def outbox_failed(): _outbox_failed.inc()
def subscriber_error(): _subscriber_errors.inc()
def replay_queue_size(value): _replay_queue.set(max(0,int(value)))

def observe_crm(outcome, elapsed):
 _crm_requests.inc()
 _crm_latency.observe(max(0,elapsed))
 if outcome=='success': _crm_success.inc()
 elif outcome=='unknown': _crm_unknown.inc()
 else: _crm_failed.inc()

def prevent_crm_duplicate(): _crm_duplicates.inc()
def crm_reconciliation_required(): _crm_reconciliation.inc()

def observe_email(outcome,elapsed):
 _email_send.inc()
 _email_latency.observe(max(0,elapsed))
 if outcome=='success': _email_success.inc()
 elif outcome=='unknown': _email_unknown.inc()
 elif outcome=='retryable': _email_failed.inc()
 else: _email_failed.inc()

def prevent_email_duplicate(): _email_duplicates.inc()
def email_reconciliation_required(): _email_reconciliation.inc()

def observe_http_provider(outcome,elapsed):
 _http_requests.inc()
 _http_latency.observe(max(0,elapsed))
 if outcome=='success': _http_success.inc()
 elif outcome=='unknown': _http_unknown.inc()
 else: _http_failed.inc()

def _sync_outbox(repo):
 if not hasattr(repo,'connection'):
  return
 with repo.connection() as conn:
  row=conn.execute("""SELECT
    (SELECT count(*) FROM jarvis_outbox_events) AS events,
    (SELECT count(*) FROM jarvis_outbox_deliveries WHERE status='PUBLISHED') AS replayed,
    (SELECT count(*) FROM jarvis_outbox_audit
      WHERE action IN ('retry_scheduled','permanent_failure')) AS failed,
    (SELECT coalesce(sum(error_count),0) FROM jarvis_event_subscribers) AS errors,
    (SELECT count(*) FROM jarvis_outbox_events e WHERE e.status='PENDING'
      OR EXISTS (SELECT 1 FROM jarvis_outbox_deliveries d
        WHERE d.event_id=e.event_id AND d.status IN ('PENDING','PROCESSING'))) AS pending""").fetchone()
 for key,counter in (('events',_outbox_events),('replayed',_outbox_replayed),
                     ('failed',_outbox_failed),('errors',_subscriber_errors)):
  with _metric_lock:
   delta=max(0,int(row[key])-_outbox_last[key])
   if delta: counter.inc(delta)
   _outbox_last[key]=int(row[key])
 _outbox_pending.set(row['pending'])
 _replay_queue.set(row['pending'])

def observe_telegram(outcome, elapsed):
 _telegram_send.inc()
 _telegram_latency.observe(max(0,elapsed))
 if outcome=='success': _telegram_success.inc()
 elif outcome=='unknown': _telegram_unknown.inc()
 else: _telegram_failure.inc()

def prevent_telegram_duplicate(): _telegram_duplicates.inc()

def _sync_counter(counter,name,value):
 with _metric_lock:
  delta=max(0,int(value)-_last_persisted[name])
  if delta:
   counter.inc(delta)
   _last_persisted[name]=int(value)

def _reconciliation_totals(repo):
 with repo.connection() as conn:
  return conn.execute("""SELECT
   count(*) FILTER(WHERE action IN ('reconcile_started','confirm-delivered','confirm-undelivered','request-new-approval')) AS reconciliation_total,
   count(*) FILTER(WHERE action IN ('confirm-delivered','confirm-undelivered')) AS reconciliation_success_total,
   count(*) FILTER(WHERE action='reconcile_failed') AS reconciliation_failed_total,
   count(*) FILTER(WHERE action='confirm-delivered') AS delivery_confirmed_total,
   count(*) FILTER(WHERE action='confirm-undelivered') AS delivery_rejected_total,
   count(*) FILTER(WHERE new_state='RECONCILIATION_REQUIRED' OR to_state='RECONCILIATION_REQUIRED') AS reconciliation_required_total,
   count(*) FILTER(WHERE action='reconcile_started') AS manual_review_total,
   count(*) FILTER(WHERE action IN ('provider_unknown','delivery_uncertain')
     OR (to_state='RECONCILIATION_REQUIRED' AND provider IS NOT NULL)) AS provider_unknown_total
   FROM jarvis_return_action_events""").fetchone()

def render_metrics(repo):
 s=repo.stats()
 values=(sum(s.get(k,0) for k in ('OPEN','READY','APPROVED','RETRY')),s.get('oldest_task',0),
  s.get('PROCESSING',0),s.get('FAILED',0),s.get('RECONCILIATION_REQUIRED',0),s.get('retry_count',0),s.get('recovery',0),s.get('approve',0))
 for name,value in zip(NAMES,values): _gauges[name].set(value)
 _sync_outbox(repo)
 totals=_reconciliation_totals(repo) if hasattr(repo,'connection') else {name:0 for name in _reconciliation_metrics}
 for name,counter in _reconciliation_metrics.items(): _sync_counter(counter,name,totals[name])
 return generate_latest(_registry)


_webhook_requests=Counter('webhook_requests_total','Webhook HTTP attempts',registry=_registry)
_webhook_success=Counter('webhook_success_total','Successful webhook operations',registry=_registry)
_webhook_failed=Counter('webhook_failed_total','Definitively failed webhook operations',registry=_registry)
_webhook_unknown=Counter('webhook_unknown_total','Webhook operations requiring reconciliation',registry=_registry)
_webhook_replayed=Counter('webhook_replayed_total','Webhook operations materialized from outbox by Replay Worker',registry=_registry)
_webhook_duplicates=Counter('webhook_duplicate_prevented_total','Duplicate webhook intents prevented',registry=_registry)
_webhook_latency=Histogram('webhook_latency_seconds','Webhook request latency',registry=_registry)
def webhook_request_attempt(): _webhook_requests.inc()
def observe_webhook(outcome,elapsed):
 _webhook_latency.observe(max(0,elapsed))
 if outcome=='success': _webhook_success.inc()
 elif outcome=='unknown': _webhook_unknown.inc()
 elif outcome=='retryable': _webhook_failed.inc()
 else: _webhook_failed.inc()
def webhook_replayed(): _webhook_replayed.inc()
def prevent_webhook_duplicate(): _webhook_duplicates.inc()
