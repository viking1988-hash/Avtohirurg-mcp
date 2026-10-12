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
 totals=_reconciliation_totals(repo) if hasattr(repo,'connection') else {name:0 for name in _reconciliation_metrics}
 for name,counter in _reconciliation_metrics.items(): _sync_counter(counter,name,totals[name])
 return generate_latest(_registry)
