import os
from unittest.mock import patch

import pytest

from jarvis_return_worker.config import Config

PROJECT = 'cee2fad7-8d02-4a3b-a70c-09dee0312cf1'
ENVIRONMENT = 'eff27b7a-7561-4863-84c6-8a4bcc6bb5e1'
URL = 'postgresql://jarvis_test:local-test-only@jarvis-pr47-postgres-disposable.railway.internal/jarvis_pr47_e2e'


def settings(**overrides):
    env = {
        'RAILWAY_PROJECT_ID': PROJECT,
        'RAILWAY_ENVIRONMENT_ID': ENVIRONMENT,
        'JARVIS_SANDBOX_PROJECT_ID': PROJECT,
        'JARVIS_SANDBOX_ENVIRONMENT_ID': ENVIRONMENT,
        'JARVIS_SANDBOX_DATABASE_URL': URL,
        'JARVIS_TRANSPORT': 'fake',
        'JARVIS_READ_TOKEN': 'r' * 40,
        'JARVIS_APPROVAL_TOKEN': 'a' * 40,
        'JARVIS_OPERATOR_ID': 'pr47-synthetic',
    }
    env.update(overrides)
    return env


def test_disposable_identity_is_allowed_only_with_fake_transport():
    with patch.dict(os.environ, settings(), clear=True):
        assert Config.from_env().database_url == URL


@pytest.mark.parametrize('change', [
    {'JARVIS_TRANSPORT': 'telegram'},
    {'JARVIS_TELEGRAM_BOT_TOKEN': 'should-not-exist'},
    {'JARVIS_TELEGRAM_TEST_CHAT_ID': '12345'},
    {'JARVIS_CRM_SANDBOX_URL': 'must-not-exist'},
    {'JARVIS_CRM_SANDBOX_TOKEN': 'must-not-exist'},
    {'JARVIS_EMAIL_SANDBOX_SMTP_HOST': 'must-not-exist'},
    {'JARVIS_HTTP_SANDBOX_URL': 'must-not-exist'},
    {'JARVIS_HTTP_SANDBOX_TOKEN': 'must-not-exist'},
    {'JARVIS_WEBHOOK_BASE_URL': 'must-not-exist'},
    {'JARVIS_WEBHOOK_TOKEN': 'must-not-exist'},
    {'RAILWAY_PROJECT_ID': 'wrong'},
    {'RAILWAY_ENVIRONMENT_ID': 'wrong'},
    {'JARVIS_SANDBOX_PROJECT_ID': 'wrong'},
    {'JARVIS_SANDBOX_ENVIRONMENT_ID': 'wrong'},
    {'JARVIS_SANDBOX_DATABASE_URL': 'postgresql://jarvis_test:pw@production.railway.internal/jarvis_pr47_e2e'},
    {'JARVIS_SANDBOX_DATABASE_URL': 'postgresql://jarvis_sandbox:pw@jarvis-pr47-postgres-disposable.railway.internal/jarvis_pr47_e2e'},
    {'JARVIS_SANDBOX_DATABASE_URL': 'postgresql://jarvis_test:pw@jarvis-pr47-postgres-disposable.railway.internal/other'},
    {'JARVIS_SANDBOX_DATABASE_URL': 'postgresql://jarvis_test:pw@jarvis-pr47-postgres-disposable.railway.internal/jarvis_pr47_e2e?sslmode=disable'},
    {'JARVIS_READ_TOKEN': 'short'},
    {'JARVIS_APPROVAL_TOKEN': 'r' * 40},
])
def test_disposable_identity_rejects_unsafe_config(change):
    with patch.dict(os.environ, settings(**change), clear=True):
        with pytest.raises(ValueError):
            Config.from_env()


# The disposable allowlist must not weaken the pre-existing Sandbox gate.
SANDBOX_URL = 'postgresql://jarvis_sandbox:local-test-only@jarvis-sandbox-postgres.railway.internal/jarvis_return_queue_test'


def test_original_sandbox_identity_remains_allowed():
    env = settings(
        RAILWAY_PROJECT_ID='original-sandbox-project',
        JARVIS_SANDBOX_PROJECT_ID='original-sandbox-project',
        RAILWAY_ENVIRONMENT_ID='original-sandbox-environment',
        JARVIS_SANDBOX_ENVIRONMENT_ID='original-sandbox-environment',
        JARVIS_SANDBOX_DATABASE_URL=SANDBOX_URL,
    )
    with patch.dict(os.environ, env, clear=True):
        assert not Config.isolated_e2e()
        assert Config.from_env().database_url == SANDBOX_URL


@pytest.mark.parametrize('database_url', [URL, SANDBOX_URL.replace('jarvis_sandbox:', 'jarvis_test:')])
def test_original_sandbox_rejects_disposable_and_mismatched_database(database_url):
    env = settings(
        RAILWAY_PROJECT_ID='original-sandbox-project',
        JARVIS_SANDBOX_PROJECT_ID='original-sandbox-project',
        RAILWAY_ENVIRONMENT_ID='original-sandbox-environment',
        JARVIS_SANDBOX_ENVIRONMENT_ID='original-sandbox-environment',
        JARVIS_SANDBOX_DATABASE_URL=database_url,
    )
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError):
            Config.from_env()
