"""Repository layer — tenant-scoped data access.

Every cross-tenant table (api_keys, voices, usage_records, audit_log,
job_idempotency) goes through a repo that REQUIRES tenant_id in its
constructor. There is no `list_all()` or `get_by_id_without_tenant`
escape hatch — those would violate D-08 (mandatory tenant filter).

The system tables (tenants, operators, talent_contracts) have their own
admin-scoped repos called only from operator endpoints (JWT-authenticated)
or from app-layer validation paths.
"""

from .api_key import ApiKeyRepo
from .audit import AuditRepo
from .data_deletion import DataDeletionRequestRepo
from .idempotency import IdempotencyConflict, IdempotencyRepo
from .operator import OperatorRepo
from .talent_contract import TalentContractRepo
from .tenant import TenantRepo
from .usage import UsageRepo
from .voice import VoiceRepo, lifecycle_state
from .voice_access import VoiceAccessRepo
from .voice_consent import VoiceConsentRecordRepo

__all__ = [
    "TenantRepo",
    "ApiKeyRepo",
    "VoiceRepo",
    "VoiceAccessRepo",
    "VoiceConsentRecordRepo",
    "TalentContractRepo",
    "DataDeletionRequestRepo",
    "UsageRepo",
    "AuditRepo",
    "OperatorRepo",
    "IdempotencyRepo",
    "IdempotencyConflict",
    "lifecycle_state",
]
