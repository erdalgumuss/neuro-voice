"""Repository layer — tenant-scoped data access.

Every cross-tenant table (api_keys, voices, usage_records, audit_log,
job_idempotency) goes through a repo that REQUIRES tenant_id in its
constructor. There is no `list_all()` or `get_by_id_without_tenant`
escape hatch — those would violate D-08 (mandatory tenant filter).

The system tables (tenants, operators) have their own admin-scoped repos
called only from operator endpoints (JWT-authenticated).
"""

from .api_key import ApiKeyRepo
from .audit import AuditRepo
from .idempotency import IdempotencyRepo
from .operator import OperatorRepo
from .tenant import TenantRepo
from .usage import UsageRepo
from .voice import VoiceRepo

__all__ = [
    "TenantRepo",
    "ApiKeyRepo",
    "VoiceRepo",
    "UsageRepo",
    "AuditRepo",
    "OperatorRepo",
    "IdempotencyRepo",
]
