import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit, unquote


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)
    read_token: str = field(repr=False)
    approval_token: str = field(repr=False)
    operator_id: str
    worker_id: str
    lease_seconds: int = 60
    heartbeat_seconds: int = 10
    recovery_seconds: int = 15
    poll_seconds: float = 1
    max_attempts: int = 5
    shutdown_seconds: int = 20

    @classmethod
    def from_env(cls):
        if (not os.environ.get('JARVIS_SANDBOX_PROJECT_ID') or not os.environ.get('JARVIS_SANDBOX_ENVIRONMENT_ID')
            or os.environ.get('RAILWAY_PROJECT_ID') != os.environ['JARVIS_SANDBOX_PROJECT_ID']
            or os.environ.get('RAILWAY_ENVIRONMENT_ID') != os.environ['JARVIS_SANDBOX_ENVIRONMENT_ID']):
            raise ValueError('worker requires the exact Railway Sandbox project and environment')
        if os.environ.get('JARVIS_TRANSPORT') != 'fake':
            raise ValueError('only Fake Transport is enabled')
        c = cls(os.environ.get('JARVIS_SANDBOX_DATABASE_URL', ''),
                os.environ.get('JARVIS_READ_TOKEN', ''), os.environ.get('JARVIS_APPROVAL_TOKEN', ''),
                os.environ.get('JARVIS_OPERATOR_ID', ''), os.environ.get('RAILWAY_REPLICA_ID', 'sandbox-worker'),
                int(os.environ.get('JARVIS_LEASE_SECONDS', '60')),
                int(os.environ.get('JARVIS_HEARTBEAT_SECONDS', '10')),
                int(os.environ.get('JARVIS_RECOVERY_SECONDS', '15')),
                float(os.environ.get('JARVIS_POLL_SECONDS', '1')),
                int(os.environ.get('JARVIS_MAX_ATTEMPTS', '5')),
                int(os.environ.get('JARVIS_SHUTDOWN_SECONDS', '20')))
        c.validate()
        return c

    def validate(self):
        p = urlsplit(self.database_url)
        if (p.scheme not in ('postgres', 'postgresql') or p.hostname != 'jarvis-sandbox-postgres.railway.internal'
            or unquote(p.path.lstrip('/')) != 'jarvis_return_queue_test'
            or unquote(p.username or '') != 'jarvis_sandbox' or not p.password or p.query or p.fragment):
            raise ValueError('refusing non-sandbox or ambiguous database URL')
        if min(len(self.read_token), len(self.approval_token)) < 32 or self.read_token == self.approval_token or not self.operator_id:
            raise ValueError('distinct strong read and human approval credentials required')
        if not (5 <= self.lease_seconds <= 300 and 1 <= self.heartbeat_seconds < self.lease_seconds / 2
                and 1 <= self.recovery_seconds <= 300 and 0.1 <= self.poll_seconds <= 60
                and 1 <= self.max_attempts <= 20 and 5 <= self.shutdown_seconds <= 60):
            raise ValueError('invalid worker timing or retry bounds')
