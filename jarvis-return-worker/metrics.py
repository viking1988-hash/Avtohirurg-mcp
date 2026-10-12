from prometheus_client import CollectorRegistry, Gauge, Counter, Histogram, generate_latest

NAMES=('queue_size','oldest_task','processing','failed','reconciliation_required','retry_count','recovery_count','approval_count')

_registry=CollectorRegistry()
_telegram_send=Counter('telegram_send_total','Telegram send attempts',registry=_registry)
_telegram_success=Counter('telegram_send_success_total','Telegram sends accepted',registry=_registry)
_telegram_failure=Counter('telegram_send_failure_total','Telegram confirmed failures',registry=_registry)
_telegram_unknown=Counter('telegram_send_unknown_total','Telegram outcomes requiring reconciliation',registry=_registry)
_telegram_duplicates=Counter('telegram_duplicate_prevented_total','Duplicate Telegram sends prevented',registry=_registry)
_telegram_latency=Histogram('telegram_api_latency_seconds','Telegram Bot API latency',registry=_registry)
_gauges={name:Gauge(name,'Jarvis Sandbox '+name,registry=_registry) for name in NAMES}

def observe_telegram(outcome, elapsed):
    _telegram_send.inc()
    _telegram_latency.observe(max(0, elapsed))
    if outcome == 'success': _telegram_success.inc()
    elif outcome == 'unknown': _telegram_unknown.inc()
    else: _telegram_failure.inc()

def prevent_telegram_duplicate():
    _telegram_duplicates.inc()

def render_metrics(repo):
    # Persisted DB totals survive restart; gauges intentionally avoid process-counter reset errors.
    s=repo.stats()
    values=(sum(s.get(k,0) for k in ('OPEN','READY','APPROVED','RETRY')),
        s.get('oldest_task',0),s.get('PROCESSING',0),s.get('FAILED',0),s.get('RECONCILIATION_REQUIRED',0),
        s.get('retry_count',0),s.get('recovery',0),s.get('approve',0))
    for name,value in zip(NAMES,values):
        _gauges[name].set(value)
    return generate_latest(_registry)
