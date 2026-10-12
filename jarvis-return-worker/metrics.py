from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

GAUGES=('queue_size','oldest_task','processing','failed','reconciliation_required','retry_count','recovery_count','approval_count')
PERSISTED_COUNTERS=(
    'reconciliation_required_total',
    'delivery_receipt_saved_total',
    'duplicate_delivery_prevented_total',
    'approval_expired_total',
    'lease_recovery_total',
)

def render_metrics(repo):
    # Counters are reconstructed from the append-only DB journal and survive worker restarts.
    s=repo.stats()
    registry=CollectorRegistry()
    values=(sum(s.get(k,0) for k in ('OPEN','READY','APPROVED','RETRY')),
        s.get('oldest_task',0),s.get('PROCESSING',0),s.get('FAILED',0),
        s.get('RECONCILIATION_REQUIRED',0),s.get('retry_count',0),
        s.get('recovery',0),s.get('approve',0))
    for name,value in zip(GAUGES,values):
        Gauge(name,'Jarvis Sandbox '+name,registry=registry).set(value)
    for name in PERSISTED_COUNTERS:
        # Prometheus Counter appends _total to the exposed metric name.
        Counter(name.removesuffix('_total'),'Persisted Jarvis Sandbox '+name,registry=registry).inc(
            s.get(name,0))
    return generate_latest(registry)
