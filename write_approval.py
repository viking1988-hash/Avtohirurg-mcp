"""Fail-closed guard for external writes until a server-side human approval flow exists."""


def require_human_approval() -> None:
    """Deny external writes because this deployment has no verifiable approval backend."""
    raise PermissionError("server_side_human_approval_required")
