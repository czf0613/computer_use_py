"""Structured failures for the opt-in window API; legacy APIs are unchanged."""

from typing import Literal

DispatchState = Literal["not_dispatched", "accepted", "unknown"]
RetryHint = Literal["never", "refresh", "after_permission", "after_close"]

ERROR_CODES = frozenset(
    {
        "backend_unavailable",
        "backend_protocol_error",
        "wrong_event_loop",
        "unsupported_background_operation",
        "background_not_verified",
        "identity_unverifiable",
        "ambiguous_window",
        "ambiguous_element",
        "element_not_found",
        "stale_window",
        "window_closed",
        "stale_element",
        "stale_geometry",
        "geometry_changed",
        "mapping_unavailable",
        "window_minimized",
        "modal_blocked",
        "permission_denied",
        "capture_unavailable",
        "element_not_editable",
        "capture_timeout",
        "outcome_unknown",
        "interference_detected",
        "backend_busy",
        "session_closed",
        "resource_limit",
    }
)


class WindowError(Exception):
    """A failure with routing and dispatch information, without target text.

    ``unknown`` may mean an action already took effect. Never retry it
    automatically. Tokens/operation IDs are diagnostic, not reusable selectors.
    """

    def __init__(
        self,
        code: str,
        operation: str,
        *,
        window_token: str | None = None,
        native_domain: str | None = None,
        native_code: int | str | None = None,
        dispatch_state: DispatchState = "not_dispatched",
        retry_hint: RetryHint = "never",
        operation_id: str | None = None,
    ) -> None:
        self.code = code
        self.operation = operation
        self.window_token = window_token
        self.native_domain = native_domain
        self.native_code = native_code
        self.dispatch_state = dispatch_state
        self.retry_hint = retry_hint
        self.operation_id = operation_id
        super().__init__(f"{operation}: {code}")


class WindowPermissionError(WindowError):
    """A permission precondition failed; this API never requests permissions."""


class WindowTimeoutError(WindowError):
    """A capture timed out or a dispatched action has an unknown outcome."""


def _error(code: str, operation: str, **kwargs) -> WindowError:
    cls = WindowError
    if code == "permission_denied":
        cls = WindowPermissionError
    elif code in {"capture_timeout", "outcome_unknown"}:
        cls = WindowTimeoutError
    return cls(code, operation, **kwargs)
