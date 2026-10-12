"""Domain errors. Each carries a stable machine code and an HTTP status; the API layer
renders them into one error envelope so clients (including the Angular UI) can branch on `code`.
"""

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details


class UnauthenticatedError(DomainError):
    status_code = 401
    code = "UNAUTHENTICATED"


class ForbiddenError(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(DomainError):
    """State/precondition conflict. Safe to surface; the client should re-read and decide."""

    status_code = 409
    code = "CONFLICT"


class PolicyViolationError(ConflictError):
    """A governance rule (approval, promotion order, rollback safety) blocks the action."""

    code = "POLICY_VIOLATION"


class ValidationFailedError(DomainError):
    status_code = 422
    code = "VALIDATION_ERROR"


class RuntimeTransientError(Exception):
    """Runtime failure worth retrying (5xx, connectivity)."""


class RuntimePermanentError(Exception):
    """Runtime rejected the request; retrying cannot help."""


class RuntimeTimeoutError(RuntimeTransientError):
    """Runtime did not respond within the deadline."""
