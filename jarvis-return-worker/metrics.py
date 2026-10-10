from prometheus_client import CollectorRegistry, Gauge, generate_latest

NAMES=('queue_size','oldest_task','processing','failed','retry_count','recovery_count','approval_count')

def render_metrics(repo):
    # Persisted DB totals survive restart; gauges intentionally avoid process-counter reset errors.
    s=repo.stats()
    values=(sum(s.get(k,0) for k in ('OPEN','READY','APPROVED','RETRY')),
        s.get('oldest_task',0),s.get('PROCESSING',0),s.get('FAILED',0),
        s.get('retry_count',0),s.get('recovery',0),s.get('approve',0))
    registry=CollectorRegistry()
    for name,value in zip(NAMES,values):
        Gauge(name,'Jarvis Sandbox '+name,registry=registry).set(value)
    return generate_latest(registry)
