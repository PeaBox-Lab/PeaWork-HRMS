from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..domain import AuditAction, DocumentCategory
from ..models import Document, DocumentAcknowledgement, Employee, User
from ..security import utcnow
from . import audit, policy
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError

EXTENSION_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _safe_display_name(filename: str) -> str:
    name = os.path.basename(filename or "document")
    cleaned = "".join(c for c in name if c.isalnum() or c in "._- ").strip()
    return (cleaned or "document")[:180]


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def _validate_type(filename: str, content_type: str | None) -> tuple[str, str]:
    settings = get_settings()
    extension = _extension(filename)
    if extension not in EXTENSION_TYPES:
        raise ValidationError(f"File type {extension or 'unknown'} is not allowed")
    expected = EXTENSION_TYPES[extension]
    declared = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    if declared not in settings.allowed_content_types:
        raise ValidationError(f"Content type {declared} is not allowed")
    # Reject obvious declared/extension mismatches (magic-byte scanning is R2).
    if declared != expected and declared != "application/octet-stream":
        raise ValidationError("File extension and content type do not match")
    return extension, expected


def save_document(
    db: Session,
    *,
    actor: User,
    employee_id: UUID,
    title: str,
    category: DocumentCategory,
    upload: UploadFile,
    expires_on: date | None = None,
    request=None,
) -> Document:
    settings = get_settings()
    employee = db.get(Employee, employee_id)
    if employee is None or employee.org_id != actor.org_id:
        raise NotFoundError("Employee not found")
    if not policy.can_upload_document_for(actor, employee):
        raise ForbiddenError("You may not upload documents for this employee")

    filename = _safe_display_name(upload.filename or "document")
    extension, expected_type = _validate_type(filename, upload.content_type)
    if not title.strip():
        raise ValidationError("Document title is required")

    root = settings.storage_path
    root.mkdir(parents=True, exist_ok=True)
    key = f"{actor.org_id}/{employee_id}/{uuid4().hex}{extension}"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise ValidationError(
                        f"File exceeds the {settings.max_upload_bytes} byte upload limit"
                    )
                digest.update(chunk)
                tmp.write(chunk)
        if size == 0:
            raise ValidationError("The uploaded file is empty")
        os.replace(tmp_path, target)
        tmp_path = None
        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    document = Document(
        org_id=actor.org_id,
        employee_id=employee_id,
        category=category,
        title=title.strip()[:160],
        filename=filename,
        storage_key=key,
        content_type=expected_type,
        size_bytes=size,
        checksum_sha256=digest.hexdigest(),
        expires_on=expires_on,
        uploaded_by=actor.id,
    )
    db.add(document)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="document",
        entity_id=document.id,
        details={
            "employee_id": str(employee_id),
            "category": category.value,
            "size_bytes": size,
            "checksum": digest.hexdigest(),
        },
        request=request,
        org_id=actor.org_id,
    )
    return document


def list_documents(
    db: Session, *, actor: User, employee_id: UUID | None = None
) -> list[Document]:
    stmt = select(Document).where(Document.org_id == actor.org_id)
    if employee_id:
        employee = db.get(Employee, employee_id)
        if employee is None or not policy.can_view_employee(actor, employee):
            raise ForbiddenError("You do not have access to this employee's documents")
        stmt = stmt.where(Document.employee_id == employee_id)
    elif not policy.is_privileged_reader(actor):
        if actor.employee_id is None:
            return []
        if actor.role.value == "manager":
            from sqlalchemy import or_

            team_ids = select(Employee.id).where(
                Employee.org_id == actor.org_id,
                or_(Employee.id == actor.employee_id, Employee.manager_id == actor.employee_id),
            )
            stmt = stmt.where(Document.employee_id.in_(team_ids))
        else:
            stmt = stmt.where(Document.employee_id == actor.employee_id)
    return list(db.scalars(stmt.order_by(Document.created_at.desc()).limit(200)).all())


def get_document(db: Session, *, actor: User, document_id: UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.org_id != actor.org_id:
        raise NotFoundError("Document not found")
    if not policy.can_view_document(actor, document):
        raise ForbiddenError("You do not have access to this document")
    return document


def file_path(document: Document) -> Path:
    settings = get_settings()
    root = settings.storage_path.resolve()
    path = (root / document.storage_key).resolve()
    if not str(path).startswith(str(root) + os.sep):
        raise ValidationError("Invalid storage key")
    if not path.exists():
        raise NotFoundError("Document file is missing from storage")
    return path


def delete_document(
    db: Session, *, actor: User, document_id: UUID, request=None
) -> None:
    if actor.role.value not in {"admin", "hr"}:
        raise ForbiddenError("Only HR or administrators may delete documents")
    document = get_document(db, actor=actor, document_id=document_id)
    path = file_path(document)
    db.delete(document)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.DELETE,
        entity_type="document",
        entity_id=document_id,
        details={"filename": document.filename},
        request=request,
        org_id=actor.org_id,
    )
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def acknowledge_document(
    db: Session,
    *,
    actor: User,
    document_id: UUID,
    request=None,
) -> DocumentAcknowledgement:
    """Record that the acting user has read and acknowledged a document.

    Employees acknowledge their own documents. HR/admin may acknowledge on
    behalf of the employee. The document summary columns are updated and an
    audit event is recorded.

    Unique constraint: one acknowledgement per document per employee
    (regardless of who acknowledges).
    """
    document = db.get(Document, document_id)
    if document is None or document.org_id != actor.org_id:
        raise NotFoundError("Document not found")

    # Acknowledgement actor must be able to view the document.
    if not policy.can_view_document(actor, document):
        raise ForbiddenError("You do not have access to this document")

    # Determine the employee this acknowledgement is for.
    # If the actor is the document's employee, it's self-acknowledgement.
    # If actor is HR/admin, it's on-behalf of the document's employee.
    doc_employee_id = document.employee_id
    is_actor_employee = actor.employee_id == doc_employee_id

    # Check for existing acknowledgement by any user for this document+employee combo.
    if is_actor_employee:
        # Employee: check if they already acknowledged their own document
        existing = db.scalar(
            select(DocumentAcknowledgement).where(
                DocumentAcknowledgement.document_id == document_id,
                DocumentAcknowledgement.acknowledger_employee_id == actor.employee_id,
            )
        )
    else:
        # HR/admin: check if any acknowledgement exists for this document+employee
        existing = db.scalar(
            select(DocumentAcknowledgement).where(
                DocumentAcknowledgement.document_id == document_id,
                DocumentAcknowledgement.acknowledger_employee_id == doc_employee_id,
            )
        )

    if existing is not None:
        raise ConflictError("Document already acknowledged")

    # Employees acknowledge for themselves; HR/admin may acknowledge on behalf
    # of the person the document belongs to.
    if doc_employee_id and actor.employee_id and actor.employee_id != doc_employee_id:
        if actor.role.value not in {"admin", "hr"}:
            raise ForbiddenError("You may only acknowledge documents that belong to you")

    # Determine the acknowledger_employee_id to store.
    # If actor is the document's employee, store their employee_id.
    # If actor is HR/admin, store the document's employee_id.
    if is_actor_employee:
        acknowledger_employee_id = actor.employee_id
    else:
        acknowledger_employee_id = doc_employee_id

    acknowledgment = DocumentAcknowledgement(
        org_id=document.org_id,
        document_id=document_id,
        acknowledger_id=actor.id,
        acknowledger_employee_id=acknowledger_employee_id,
        acknowledged_at=utcnow(),
        ip=getattr(request.client, "host", None) if request else None,
        user_agent=(request.headers.get("user-agent") or None) if request else None,
    )
    db.add(acknowledgment)

    document.acknowledged_at = acknowledgment.acknowledged_at
    document.acknowledged_by = actor.id

    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.VIEW,
        entity_type="document_acknowledgement",
        entity_id=document_id,
        details={
            "employee_id": str(document.employee_id),
            "acknowledged": True,
        },
        request=request,
        org_id=document.org_id,
    )
    db.flush()
    return acknowledgment


def list_acknowledgements(
    db: Session,
    *,
    actor: User,
    document_id: UUID | None = None,
    employee_id: UUID | None = None,
) -> list[DocumentAcknowledgement]:
    """List acknowledgements.

    Employees see only their own acknowledgements; HR/admin see everything.
    """
    if not policy.is_privileged_reader(actor):
        if actor.employee_id is None:
            return []
        if document_id:
            document = db.get(Document, document_id)
            if document is None or document.employee_id != actor.employee_id:
                raise ForbiddenError("You do not have access to this document's acknowledgements")
            employee_id = actor.employee_id
        else:
            employee_id = actor.employee_id

    stmt = select(DocumentAcknowledgement).where(DocumentAcknowledgement.org_id == actor.org_id)
    if document_id:
        stmt = stmt.where(DocumentAcknowledgement.document_id == document_id)
    if employee_id:
        stmt = stmt.where(DocumentAcknowledgement.acknowledger_employee_id == employee_id)
    return list(
        db.scalars(stmt.order_by(DocumentAcknowledgement.acknowledged_at.desc())).all()
    )


def get_acknowledgement(
    db: Session,
    *,
    actor: User,
    document_id: UUID,
) -> DocumentAcknowledgement | None:
    """Get the acknowledgement for a document, scoped by visibility."""
    stmt = select(DocumentAcknowledgement).where(
        DocumentAcknowledgement.document_id == document_id,
        DocumentAcknowledgement.org_id == actor.org_id,
    )
    if not policy.is_privileged_reader(actor):
        if actor.employee_id is None:
            return None
        # Employee can see acknowledgements for documents that belong to them.
        document = db.get(Document, document_id)
        if document is None or document.employee_id != actor.employee_id:
            raise ForbiddenError("You do not have access to this document's acknowledgements")
    return db.scalar(stmt)


def document_has_been_acknowledged(db: Session, document_id: UUID) -> bool:
    """Check if a document has at least one acknowledgement."""
    return (
        db.scalar(
            select(DocumentAcknowledgement.id).where(
                DocumentAcknowledgement.document_id == document_id
            )
        )
        is not None
    )


__all__ = [
    "acknowledge_document",
    "delete_document",
    "document_has_been_acknowledged",
    "file_path",
    "get_acknowledgement",
    "get_document",
    "list_acknowledgements",
    "list_documents",
    "save_document",
]
