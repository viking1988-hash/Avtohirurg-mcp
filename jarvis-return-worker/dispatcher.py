"""Registry based transport selection; unknown or disabled providers fail closed."""
from dataclasses import dataclass
from .provider import ProviderAdapter


class DispatcherError(RuntimeError):
    pass


@dataclass(frozen=True)
class Registration:
    provider: object
    enabled: bool


class TransportDispatcher:
    def __init__(self):
        self._providers = {}

    def register(self, name, provider, *, enabled=True):
        if not name or not hasattr(provider, "send") and not hasattr(provider, "deliver"):
            raise ValueError("provider name and send-capable client are required")
        self._providers[name] = Registration(ProviderAdapter(provider), bool(enabled))

    def dispatch(self, name):
        registration = self._providers.get(name)
        if registration is None:
            raise DispatcherError("provider_not_registered")
        if not registration.enabled:
            raise DispatcherError("provider_disabled")
        return registration.provider

    def names(self):
        return tuple(sorted(self._providers))
