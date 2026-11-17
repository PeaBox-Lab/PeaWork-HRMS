from __future__ import annotations


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ForbiddenError(DomainError):
    status_code = 403


class ConflictError(DomainError):
    status_code = 409


class ValidationError(DomainError):
    status_code = 422


class BulkImportError(ValidationError):
    """Raised when one or more import rows fail validation."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(
            f"{len(self.errors)} row(s) failed validation; no employees were imported."
        )


__all__ = [
    "BulkImportError",
    "ConflictError",
    "DomainError",
    "ForbiddenError",
    "NotFoundError",
    "ValidationError",
]
