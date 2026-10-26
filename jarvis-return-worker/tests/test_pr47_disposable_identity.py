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
