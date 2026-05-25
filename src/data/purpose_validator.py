"""Purpose limitation validator — enforces data classification governance.

Issue #4177 — Data lake writes must include purpose metadata and be blocked
when the destination is not approved for that data class.
"""

import enum
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


class DataClassification(enum.Enum):
    """Classification of data handled in the data lake."""
    PII = "pii"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"
    ANALYTICAL = "analytical"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class PurposeCategory(enum.Enum):
    """Declared purpose for data usage."""
    AUDIT = "audit"
    BILLING = "billing"
    OPERATIONS = "operations"
    ANALYTICS = "analytics"
    COMPLIANCE = "compliance"
    PRODUCT_IMPROVEMENT = "product_improvement"
    MARKETING = "marketing"


@dataclass
class PurposeMetadata:
    """Metadata attached to each data lake write operation."""
    purpose: PurposeCategory
    data_class: DataClassification
    owner: str
    source_system: str
    destination: str
    expires_at: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class IngestionManifest:
    """Manifest for a data lake write, validated before ingestion."""
    data_id: str
    purpose_metadata: PurposeMetadata
    row_count: int
    created_at: float = field(default_factory=time.time)


class PurposeViolationError(Exception):
    """Raised when a data lake write violates purpose limitation policy."""
    def __init__(self, data_id: str, reason: str):
        self.data_id = data_id
        self.reason = reason
        super().__init__(f"Purpose violation for {data_id}: {reason}")


class DataClassificationRegistry:
    """Registry of approved data class → destination mappings."""

    def __init__(self):
        # Default policy: operational data → any, everything else restricted
        self._approved_destinations: Dict[DataClassification, Set[str]] = {
            DataClassification.OPERATIONAL: {"*"},  # wildcard: any destination
            DataClassification.SYSTEM: {"*"},
            DataClassification.ANALYTICAL: {"analytics_warehouse", "dashboard_cache"},
            DataClassification.FINANCIAL: {"billing_db", "compliance_store", "analytics_warehouse"},
            DataClassification.PII: {"compliance_store"},
            DataClassification.UNKNOWN: set(),  # blocked by default
        }

    def is_destination_approved(self, data_class: DataClassification, destination: str) -> bool:
        """Check if a destination is approved for the given data class."""
        approved = self._approved_destinations.get(data_class, set())
        if "*" in approved:
            return True
        return destination in approved

    def add_approval(self, data_class: DataClassification, destination: str) -> None:
        """Add a new approved destination for a data class."""
        if data_class not in self._approved_destinations:
            self._approved_destinations[data_class] = set()
        self._approved_destinations[data_class].add(destination)

    def remove_approval(self, data_class: DataClassification, destination: str) -> None:
        """Remove an approved destination."""
        approved = self._approved_destinations.get(data_class)
        if approved and destination in approved:
            approved.discard(destination)

    def list_approvals(self, data_class: Optional[DataClassification] = None) -> Dict[str, list]:
        """List all approvals, optionally filtered by data class."""
        result = {}
        for dc, dests in self._approved_destinations.items():
            if data_class and dc != data_class:
                continue
            result[dc.value] = sorted(dests)
        return result


class PurposeLimitationValidator:
    """Validates data lake writes against purpose limitation policy."""

    def __init__(self, registry: Optional[DataClassificationRegistry] = None):
        self.registry = registry or DataClassificationRegistry()
        self._audit_log: List[Dict] = []

    def validate(self, manifest: IngestionManifest) -> bool:
        """Validate an ingestion manifest.

        Returns True if allowed, raises PurposeViolationError if blocked.
        """
        # 1) Purpose metadata must be complete
        p = manifest.purpose_metadata
        if not p.purpose or not p.data_class or not p.destination:
            raise PurposeViolationError(
                manifest.data_id,
                f"Incomplete purpose metadata: purpose={p.purpose}, "
                f"class={p.data_class}, dest={p.destination}"
            )

        # 2) Destination must be approved for this data class
        if not self.registry.is_destination_approved(p.data_class, p.destination):
            raise PurposeViolationError(
                manifest.data_id,
                f"Destination '{p.destination}' not approved "
                f"for data class '{p.data_class.value}'"
            )

        # 3) Data class must not be UNKNOWN (explicit classification required)
        if p.data_class == DataClassification.UNKNOWN:
            raise PurposeViolationError(
                manifest.data_id,
                "Data with UNKNOWN classification cannot be written to data lake"
            )

        return True

    def permit(self, manifest: IngestionManifest) -> bool:
        """Permit a write if valid, log the decision."""
        try:
            self.validate(manifest)
            self._log("permitted", manifest, "")
            return True
        except PurposeViolationError as e:
            self._log("blocked", manifest, str(e))
            return False

    def _log(self, action: str, manifest: IngestionManifest, reason: str) -> None:
        """Record an audit entry for the decision."""
        if len(self._audit_log) >= 1000:
            self._audit_log = self._audit_log[-999:]
        self._audit_log.append({
            "action": action,
            "data_id": manifest.data_id,
            "purpose": manifest.purpose_metadata.purpose.value,
            "data_class": manifest.purpose_metadata.data_class.value,
            "destination": manifest.purpose_metadata.destination,
            "owner": manifest.purpose_metadata.owner,
            "reason": reason,
            "timestamp": time.time(),
        })

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log of all decisions."""
        return list(self._audit_log)

    def get_report(self) -> Dict:
        """Generate a report of writes grouped by purpose and owner."""
        report = {"by_purpose": {}, "by_owner": {}}
        for entry in self._audit_log:
            purpose = entry["purpose"]
            owner = entry["owner"]
            if purpose not in report["by_purpose"]:
                report["by_purpose"][purpose] = {"permitted": 0, "blocked": 0}
            if owner not in report["by_owner"]:
                report["by_owner"][owner] = {"permitted": 0, "blocked": 0}
            report["by_purpose"][purpose][entry["action"]] += 1
            report["by_owner"][owner][entry["action"]] += 1
        return report