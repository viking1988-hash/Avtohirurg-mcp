"""Fail-closed guard for external writes until a server-side human approval flow exists."""
from collections.abc import Callable
from typing import Any


def require_human_approval() -> None:
    """Deny external writes because this deployment has no verifiable approval backend."""
    raise PermissionError("server_side_human_approval_required")


def execute_approved_write(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run an external operation only after a verifiable server-side approval exists."""
    require_human_approval()
    return operation(*args, **kwargs)
