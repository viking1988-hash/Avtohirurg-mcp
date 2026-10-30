import asyncio
import io
import json
import os
import uuid
from email import message_from_bytes
from types import SimpleNamespace

import pytest
from concurrent.futures import ThreadPoolExecutor

from jarvis_return_worker.email_adapter import EmailAdapter
from jarvis_return_worker.email_provider import EmailProvider
from jarvis_return_worker.transport import Classification, DeliveryError


class FakeSMTP:
    def __init__(self, host, port, timeout, *, data_error=None, connect_error=None):
        if connect_error:
            raise connect_error
        self.host, self.port, self.timeout = host, port, timeout
        self.data_error = data_error
        self.commands=[]
        self.message=None
    def ehlo(self): self.commands.append('EHLO')
    def mail(self, sender): self.commands.append('MAIL'); return 250,b'OK'
    def rcpt(self, recipient): self.commands.append('RCPT'); return 250,b'OK'
    def data(self, message):
        self.commands.append('DATA')
        if self.data_error: raise self.data_error
        self.message=message
        return 250,b'queued as TEST-Q-42'
    def quit(self): self.commands.append('QUIT')
    def close(self): self.commands.append('CLOSE')


def provider(factory=None):
    return EmailProvider('mailpit.railway.internal',1025,'jarvis@localhost.test',
                         timeout_seconds=3,smtp_factory=factory or FakeSMTP)


def test_provider_sends_only_to_test_domain_and_returns_smtp_receipt():
    clients=[]
    def factory(*args,**kwargs):
        smtp=FakeSMTP(*args,**kwargs); clients.append(smtp); return smtp
    result=provider(factory).send({'to':'qa@example.test','subject':'Sandbox check','body':'Synthetic only'},'email-key-1')
    assert result['provider_message_id']=='TEST-Q-42'
    assert result['smtp_message_id']=='<'+__import__('hashlib').sha256(b'email-key-1').hexdigest()+'@jarvis-sandbox.invalid>'
    assert result['receipt'].startswith('smtp:accepted:')
    assert result['provider_status']=='accepted' and result['delivery_time']
    parsed=message_from_bytes(clients[0].message)
    assert parsed['To']=='qa@example.test' and parsed['Subject']=='Sandbox check'
    assert parsed['Message-ID']==result['smtp_message_id']
    assert clients[0].commands==['EHLO','MAIL','RCPT','DATA','QUIT']


@pytest.mark.parametrize('host',[
    'smtp.example.com','smtp-production.railway.internal','mailpit.production.internal',
])
def test_provider_rejects_non_sandbox_smtp_hosts(host):
    with pytest.raises(ValueError): EmailProvider(host,1025,'jarvis@localhost.test')


def test_provider_never_accepts_real_email_domains():
    with pytest.raises(DeliveryError) as error:
        provider().send({'to':'customer@example.com','subject':'x','body':'y'},'email-key')
    assert error.value.classification==Classification.PERMANENT


def test_connect_timeout_and_provider_unavailable_are_retryable_before_data():
    for error in (TimeoutError('network details'),OSError('connection refused')):
        def factory(*args,**kwargs): raise error
        with pytest.raises(DeliveryError) as raised:
            provider(factory).send({'to':'qa@example.test','subject':'x','body':'y'},'email-key')
        assert raised.value.classification==Classification.RETRYABLE
        assert 'network details' not in str(raised.value)


def test_smtp_disconnect_during_data_is_unknown_and_not_retriable():
    def factory(*args,**kwargs):
        return FakeSMTP(*args,**kwargs,data_error=ConnectionError('connection lost after terminator'))
    with pytest.raises(DeliveryError) as error:
        provider(factory).send({'to':'qa@example.test','subject':'x','body':'y'},'email-key')
    assert error.value.classification==Classification.UNKNOWN
    assert error.value.code=='email_smtp_outcome_unknown'


def test_standard_smtp_cancel_and_delivery_lookup_are_explicitly_limited():
    email=provider()
    with pytest.raises(DeliveryError) as error:
        email.cancel('TEST-Q-42','cancel-key')
    assert error.value.classification==Classification.PERMANENT
    assert email.checkStatus('TEST-Q-42')=='UNKNOWN'
    assert email.getReceipt('TEST-Q-42') is None
    assert email.reconcile({'smtp_message_id':'<id@jarvis-sandbox.invalid>'})['status']=='UNKNOWN'
    with pytest.raises(DeliveryError) as update_error:
        email.update({'subject':'changed'},'new-key')
    assert update_error.value.classification==Classification.PERMANENT


def test_email_adapter_creates_only_outbox_intents_and_never_calls_queue_or_provider():
    import inspect
    source=inspect.getsource(EmailAdapter.handle)
    assert 'conn.execute' in source and 'provider' not in source.lower() and 'queue' not in source.lower()
    event={'event_id':str(uuid.uuid4()),'event_type':'EmailRequested','correlation_id':'corr-email',
           'payload':{'to':'qa@example.test','subject':'Synthetic','body':'No real email'}}
    command=EmailAdapter.adapt(event)
    assert command['idempotency_key']=='jarvis-email:'+event['event_id']
    assert command['smtp_message_id'].endswith('@jarvis-sandbox.invalid>')
    for kind in ('EmailQueued','EmailSent','EmailUnknown','EmailFailed','EmailCancelled'):
        assert EmailAdapter.adapt({**event,'event_type':kind}) is None


def test_email_metrics_are_exposed():
    from jarvis_return_worker import metrics
    class Stats:
        def stats(self): return {}
    metrics.observe_email('success',.01)
    metrics.observe_email('unknown',.02)
    metrics.prevent_email_duplicate()
    metrics.email_reconciliation_required()
    output=metrics.render_metrics(Stats()).decode()
    for name in ('email_send_total','email_success_total','email_failed_total',
                 'email_unknown_total','email_duplicate_prevented_total',
                 'email_reconciliation_total','email_latency_seconds'):
        assert name in output


def test_dispatcher_registry_keeps_fake_telegram_crm_and_email_together(monkeypatch):
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv('JARVIS_TRANSPORT','fake')
    monkeypatch.setenv('JARVIS_TELEGRAM_OUTBOUND_APPROVED','1')
    monkeypatch.setenv('JARVIS_CRM_SANDBOX_URL','https://crm-sandbox.up.railway.app')
    monkeypatch.setenv('JARVIS_CRM_SANDBOX_TOKEN','synthetic-ci-token')
    monkeypatch.setenv('JARVIS_EMAIL_SANDBOX_SMTP_HOST','mailpit.railway.internal')
    config=SimpleNamespace(telegram_bot_token='synthetic-ci-bot-token',
                           telegram_test_chat_id='-1000000000000')
    dispatcher=make_dispatcher(config,None)
    assert dispatcher.names()==('crm','email','fake','telegram')
    assert dispatcher.dispatch('fake')
    assert dispatcher.dispatch('telegram')
    assert dispatcher.dispatch('crm')
    assert dispatcher.dispatch('email')


@pytest.fixture(scope='module')
def repository():
    url=os.environ.get('JARVIS_WORKER_CI_DATABASE_URL')
    if not url: pytest.skip('PostgreSQL integration URL is not configured')
    from jarvis_return_worker.queue import QueueRepository
    repo=QueueRepository(SimpleNamespace(database_url=url,worker_id='email-tests',
        lease_seconds=30,heartbeat_seconds=5,max_attempts=3))
    repo.migrate()
    yield repo
    # The CI database is shared by integration test modules; remove only this
    # module's synthetic Email events so later isolated subscribers cannot see
    # an event for which they intentionally have no handler.
    with repo.connection() as conn:
        conn.execute("""DELETE FROM jarvis_outbox_audit WHERE event_id IN
            (SELECT event_id FROM jarvis_outbox_events WHERE aggregate_type='email')""")
        conn.execute("""DELETE FROM jarvis_processed_events WHERE event_id IN
            (SELECT event_id FROM jarvis_outbox_events WHERE aggregate_type='email')""")
        conn.execute("""DELETE FROM jarvis_outbox_deliveries WHERE event_id IN
            (SELECT event_id FROM jarvis_outbox_events WHERE aggregate_type='email')""")
        conn.execute("DELETE FROM jarvis_email_operation_events")
        conn.execute("DELETE FROM jarvis_email_operations")
        conn.execute("DELETE FROM jarvis_outbox_events WHERE aggregate_type='email'")
        conn.execute("DELETE FROM jarvis_event_subscribers WHERE subscriber_name='jarvis.email-adapter'")


def materialize_email_operation(repository):
    from jarvis_return_worker.dispatcher import TransportDispatcher
    from jarvis_return_worker.events import EventBus
    from jarvis_return_worker.orchestrator import Orchestrator
    from jarvis_return_worker.outbox import OutboxReplay
    service=Orchestrator(repository,TransportDispatcher())
    suffix=str(uuid.uuid4()); causation='email-'+suffix
    event_id=service.request_email('qa@example.test','Synthetic notice','No real email is sent',
                                   'corr-'+suffix,causation)
    # Idempotent duplicate request resolves to the original outbox event.
    assert service.request_email('qa@example.test','Synthetic notice','No real email is sent',
                                 'corr-'+suffix,causation)==event_id
    registry=EventBus(); EmailAdapter().register(registry)
    replay=OutboxReplay(repository,registry,owner='email-materializer')
    replay.register_subscribers()
    for _ in range(50):
        if repository.get_email_operation(event_id): break
        assert replay.replay_one()
    assert repository.get_email_operation(event_id)
    with repository.connection() as conn:
        conn.execute("UPDATE jarvis_email_operations SET state='FAILED' WHERE state='PENDING' AND event_id<>%s",(event_id,))
    return event_id


def test_email_request_outbox_replay_and_duplicate_request_create_one_intent(repository):
    event_id=materialize_email_operation(repository)
    row=repository.get_email_operation(event_id)
    assert row['state']=='PENDING' and row['attempt_count']==0
    assert row['recipient']=='qa@example.test'
    assert row['idempotency_key']=='jarvis-email:'+event_id
    assert row['smtp_message_id'].endswith('@jarvis-sandbox.invalid>')
    assert row['provider_message_id'] is None and row['receipt'] is None
    with repository.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM jarvis_email_operations WHERE event_id=%s',(event_id,)).fetchone()['n']==1
        assert conn.execute("SELECT count(*) AS n FROM jarvis_processed_events WHERE event_id=%s AND subscriber_name='jarvis.email-adapter'",(event_id,)).fetchone()['n']==1


def test_replay_restart_reuses_durable_intent_and_processed_marker(repository):
    from jarvis_return_worker.events import EventBus
    from jarvis_return_worker.outbox import OutboxReplay
    event_id=materialize_email_operation(repository)
    row_before=repository.get_email_operation(event_id)
    # Reconstructing ReplayWorker is the process-restart boundary; committed
    # subscriber work must stay complete and must not create a second intent.
    registry=EventBus(); EmailAdapter().register(registry)
    restarted=OutboxReplay(repository,registry,owner='email-replay-after-restart')
    restarted.register_subscribers()
    assert restarted.replay_one() is False
    assert repository.get_email_operation(event_id)['event_id']==row_before['event_id']
    with repository.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM jarvis_email_operations WHERE event_id=%s',(event_id,)).fetchone()['n']==1


def test_success_persists_receipt_ids_and_smtp_acceptance_state(repository):
    event_id=materialize_email_operation(repository)
    claim=repository.claim_email_operation('email-worker-once')
    assert claim and claim['event_id']==event_id
    result=provider().send({'to':claim['recipient'],**claim['payload']},claim['idempotency_key'])
    saved=repository.finish_email_operation(event_id,'email-worker-once',claim['attempt_count'],result)
    assert saved['state']=='QUEUED'
    assert saved['provider_message_id']=='TEST-Q-42'
    assert saved['smtp_message_id']==claim['smtp_message_id']
    assert saved['receipt']==result['receipt'] and saved['provider_status']=='accepted'
    assert saved['delivery_time'] is not None


def test_timeout_reconciliation_is_versioned_and_never_resends(repository):
    from jarvis_return_worker.queue import Conflict
    from jarvis_return_worker.transport import Classification
    event_id=materialize_email_operation(repository)
    claim=repository.claim_email_operation('email-worker-timeout')
    unknown=repository.fail_email_operation(event_id,'email-worker-timeout',claim['attempt_count'],
        Classification.UNKNOWN,'email_smtp_outcome_unknown')
    assert unknown['state']=='RECONCILIATION_REQUIRED'
    assert repository.claim_email_operation('email-no-blind-retry') is None
    reconciled=repository.resolve_email_reconciliation(event_id,'sandbox-operator',unknown['version'],
        'delivered',{'receipt':'smtp:verified:test','provider_message_id':'TEST-Q-42','reason':'verified in sink log'})
    assert reconciled['state']=='SENT'
    with pytest.raises(Conflict,match='stale Email operation version'):
        repository.resolve_email_reconciliation(event_id,'stale-operator',unknown['version'],
            'not_delivered',{'reason':'stale review'})
    assert repository.claim_email_operation('email-after-reconcile') is None


def test_email_lease_timeout_fences_worker_and_never_requeues(repository):
    from jarvis_return_worker.queue import Conflict
    event_id=materialize_email_operation(repository)
    claim=repository.claim_email_operation('email-worker-before-restart')
    with repository.connection() as conn:
        conn.execute("UPDATE jarvis_email_operations SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=%s",(event_id,))
    assert repository.claim_email_operation('email-worker-after-restart') is None
    row=repository.get_email_operation(event_id)
    assert row['state']=='RECONCILIATION_REQUIRED' and row['attempt_count']==1
    with pytest.raises(Conflict,match='stale Email lease'):
        repository.finish_email_operation(event_id,'email-worker-before-restart',claim['attempt_count'],
            {'smtp_message_id':claim['smtp_message_id'],'receipt':'late','provider_status':'accepted'})


def test_two_email_workers_cannot_claim_the_same_event(repository):
    event_id=materialize_email_operation(repository)
    with ThreadPoolExecutor(max_workers=2) as workers:
        claims=list(workers.map(repository.claim_email_operation,('email-worker-a','email-worker-b')))
    owned=[claim for claim in claims if claim and claim['event_id']==event_id]
    assert len(owned)==1
    assert sum(claim is not None for claim in claims)==1


def test_cancel_only_applies_before_smtp_attempt_and_uses_optimistic_lock(repository):
    from jarvis_return_worker.queue import Conflict
    event_id=materialize_email_operation(repository)
    row=repository.get_email_operation(event_id)
    cancelled=repository.cancel_email_operation(event_id,'sandbox-operator',row['version'])
    assert cancelled['state']=='CANCELLED'
    with pytest.raises(Conflict):
        repository.cancel_email_operation(event_id,'sandbox-operator',row['version'])
    assert repository.claim_email_operation('email-cancelled-not-claimed') is None


def test_telegram_credentials_alone_do_not_enable_outbound(monkeypatch):
    """Fail closed: credentials without explicit approval cannot register Telegram."""
    from jarvis_return_worker.app import make_dispatcher
    from jarvis_return_worker.dispatcher import DispatcherError
    monkeypatch.setenv('JARVIS_TRANSPORT', 'fake')
    monkeypatch.delenv('JARVIS_TELEGRAM_OUTBOUND_APPROVED', raising=False)
    config=SimpleNamespace(telegram_bot_token='synthetic-ci-bot-token',
                           telegram_test_chat_id='-1000000000000')
    dispatcher=make_dispatcher(config,None)
    assert 'telegram' not in dispatcher.names()
    assert 'fake' in dispatcher.names()
    with pytest.raises(DispatcherError):
        dispatcher.dispatch('telegram')


def test_telegram_outbound_requires_exact_opt_in(monkeypatch):
    """Ambiguous opt-in values must not activate an external provider."""
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv('JARVIS_TRANSPORT', 'fake')
    config=SimpleNamespace(telegram_bot_token='synthetic-ci-bot-token',
                           telegram_test_chat_id='-1000000000000')
    for value in ('0', 'true', 'yes', ' 1x '):
        monkeypatch.setenv('JARVIS_TELEGRAM_OUTBOUND_APPROVED', value)
        assert 'telegram' not in make_dispatcher(config,None).names()
    monkeypatch.setenv('JARVIS_TELEGRAM_OUTBOUND_APPROVED', '1')
    assert 'telegram' in make_dispatcher(config,None).names()


def test_selected_telegram_transport_refuses_start_without_approval(monkeypatch):
    """A Telegram primary selection must not bypass the outbound approval gate."""
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv('JARVIS_TRANSPORT', 'telegram')
    monkeypatch.delenv('JARVIS_TELEGRAM_OUTBOUND_APPROVED', raising=False)
    config=SimpleNamespace(telegram_bot_token='synthetic-ci-bot-token',
                           telegram_test_chat_id='-1000000000000')
    with pytest.raises(ValueError, match='Telegram requires Sandbox bot credentials and a test chat'):
        make_dispatcher(config,None)


def test_telegram_approval_without_credentials_cannot_register(monkeypatch):
    """An approval flag alone must not create a Telegram transport."""
    from jarvis_return_worker.app import make_dispatcher
    monkeypatch.setenv('JARVIS_TRANSPORT', 'fake')
    monkeypatch.setenv('JARVIS_TELEGRAM_OUTBOUND_APPROVED', '1')
    config=SimpleNamespace(telegram_bot_token='',telegram_test_chat_id='')
    assert 'telegram' not in make_dispatcher(config,None).names()
