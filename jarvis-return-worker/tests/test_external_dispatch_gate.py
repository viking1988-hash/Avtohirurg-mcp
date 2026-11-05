"""Production runtime must fail closed while stale-worker external-effect P1 is open."""
from types import SimpleNamespace

import pytest

from jarvis_return_worker.app import make_dispatcher
from jarvis_return_worker.dispatcher import DispatcherError


def configure_external_providers(monkeypatch):
    values = {
        "JARVIS_TRANSPORT": "fake",
        "JARVIS_TELEGRAM_OUTBOUND_APPROVED": "1",
        "JARVIS_CRM_SANDBOX_URL": "https://crm.invalid",
        "JARVIS_CRM_SANDBOX_TOKEN": "synthetic-crm-token",
        "JARVIS_EMAIL_SANDBOX_SMTP_HOST": "smtp.invalid",
        "JARVIS_HTTP_SANDBOX_URL": "https://sink.invalid",
        "JARVIS_HTTP_SANDBOX_TOKEN": "synthetic-http-token",
        "JARVIS_WEBHOOK_OUTBOUND_APPROVED": "1",
        "JARVIS_WEBHOOK_BASE_URL": "https://webhook.invalid",
        "JARVIS_WEBHOOK_TOKEN": "synthetic-webhook-token",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_default_dispatcher_ignores_credentials_and_exposes_only_fake(monkeypatch):
    configure_external_providers(monkeypatch)
    config = SimpleNamespace(
        telegram_bot_token="synthetic-telegram-token",
        telegram_test_chat_id="-1000000000000",
    )

    dispatcher = make_dispatcher(config, repository=None)

    assert dispatcher.names() == ("fake",)
    for provider in ("telegram", "crm", "email", "http", "webhook"):
        with pytest.raises(DispatcherError, match="provider_not_registered"):
            dispatcher.dispatch(provider)


@pytest.mark.parametrize("selected", ["telegram", "crm", "email", "http", "webhook"])
def test_default_dispatcher_cannot_select_external_transport(monkeypatch, selected):
    configure_external_providers(monkeypatch)
    monkeypatch.setenv("JARVIS_TRANSPORT", selected)

    with pytest.raises(ValueError, match="external transports are disabled"):
        make_dispatcher(SimpleNamespace(), repository=None)
