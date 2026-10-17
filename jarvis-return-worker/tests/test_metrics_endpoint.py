import asyncio
import jarvis_return_worker.app as worker_app


class Repo:
    def stats(self):
        return {'OPEN':0,'READY':0,'APPROVED':0,'RETRY':0,'PROCESSING':0,'FAILED':0,
                'RECONCILIATION_REQUIRED':0,'retry_count':0,'recovery':0,'approve':0}


async def _asgi_get(authorization=None):
    worker_app.app.state.repository = Repo()
    headers = []
    if authorization:
        headers = [(b'authorization', authorization.encode())]
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.3'},
        'http_version': '1.1',
        'method': 'GET',
        'scheme': 'http',
        'path': '/metrics',
        'raw_path': b'/metrics',
        'query_string': b'',
        'headers': headers,
        'client': ('127.0.0.1', 12345),
        'server': ('testserver', 80),
        'app': worker_app.app,
    }
    messages = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        return {'type': 'http.disconnect'}

    async def send(message):
        messages.append(message)

    await worker_app.app(scope, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    body = b''.join(message.get('body', b'') for message in messages
                    if message['type'] == 'http.response.body')
    return start['status'], dict(start['headers']), body


def _get(authorization=None):
    return asyncio.run(_asgi_get(authorization))


def test_metrics_rejects_unauthenticated(monkeypatch):
    monkeypatch.setenv('JARVIS_METRICS_TOKEN', 'metrics-test-token')
    status, _, body = _get()
    assert status == 401
    assert b'metrics authentication required' in body


def test_metrics_does_not_accept_general_read_token(monkeypatch):
    monkeypatch.setenv('JARVIS_METRICS_TOKEN', 'metrics-test-token')
    monkeypatch.setenv('JARVIS_READ_TOKEN', 'read-test-token')
    status, _, body = _get('Bearer read-test-token')
    assert status == 401
    assert b'metrics authentication required' in body


def test_metrics_is_read_only_prometheus_text(monkeypatch):
    monkeypatch.setenv('JARVIS_METRICS_TOKEN', 'metrics-test-token')
    monkeypatch.setattr(
        worker_app,
        'render_metrics',
        lambda repo: b'outbox_pending_total 0\\nreconciliation_required_queue 0\\noutbox_failed_total 0\\n',
    )
    status, headers, body = _get('Bearer metrics-test-token')
    assert status == 200
    assert headers[b'content-type'].startswith(b'text/plain; version=0.0.4')
    text = body.decode()
    assert 'outbox_pending_total 0' in text
    assert 'reconciliation_required_queue 0' in text
    assert 'outbox_failed_total 0' in text
