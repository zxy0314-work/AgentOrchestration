"""Tests for data purpose limitation validator (issue #4177)."""

import pytest
from src.data.purpose_validator import (
    DataClassification,
    PurposeCategory,
    PurposeMetadata,
    IngestionManifest,
    DataClassificationRegistry,
    PurposeLimitationValidator,
    PurposeViolationError,
)


class TestDataClassificationRegistry:

    def test_default_approvals(self):
        """Default registry has sensible approvals."""
        reg = DataClassificationRegistry()
        assert reg.is_destination_approved(DataClassification.OPERATIONAL, "any_db")
        assert reg.is_destination_approved(DataClassification.PII, "compliance_store")
        assert not reg.is_destination_approved(DataClassification.UNKNOWN, "any_db")
        assert not reg.is_destination_approved(DataClassification.PII, "public_cache")

    def test_add_approval(self):
        """Can add new destination approval."""
        reg = DataClassificationRegistry()
        reg.add_approval(DataClassification.PII, "analytics_warehouse")
        assert reg.is_destination_approved(DataClassification.PII, "analytics_warehouse")

    def test_remove_approval(self):
        """Can remove destination approval."""
        reg = DataClassificationRegistry()
        reg.remove_approval(DataClassification.PII, "compliance_store")
        assert not reg.is_destination_approved(DataClassification.PII, "compliance_store")

    def test_list_approvals(self):
        """List approvals returns all registered destinations."""
        reg = DataClassificationRegistry()
        approvals = reg.list_approvals()
        assert DataClassification.OPERATIONAL.value in approvals
        assert "*" in approvals[DataClassification.OPERATIONAL.value]


class TestPurposeLimitationValidator:

    def setup_method(self):
        self.validator = PurposeLimitationValidator()

    def test_valid_operational_write_permitted(self):
        """Operational data to any destination is permitted."""
        manifest = IngestionManifest(
            data_id="test-001",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.OPERATIONS,
                data_class=DataClassification.OPERATIONAL,
                owner="alice",
                source_system="worker-1",
                destination="analytics_warehouse",
            ),
            row_count=1000,
        )
        assert self.validator.permit(manifest) is True

    def test_pii_to_approved_destination(self):
        """PII data to approved destination is permitted."""
        manifest = IngestionManifest(
            data_id="test-002",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.COMPLIANCE,
                data_class=DataClassification.PII,
                owner="bob",
                source_system="worker-2",
                destination="compliance_store",
            ),
            row_count=50,
        )
        assert self.validator.permit(manifest) is True

    def test_pii_to_unapproved_destination_blocked(self):
        """PII data to unapproved destination is blocked."""
        manifest = IngestionManifest(
            data_id="test-003",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.MARKETING,
                data_class=DataClassification.PII,
                owner="alice",
                source_system="worker-1",
                destination="public_cache",
            ),
            row_count=100,
        )
        assert self.validator.permit(manifest) is False

    def test_unknown_classification_blocked(self):
        """Data with UNKNOWN classification is blocked."""
        manifest = IngestionManifest(
            data_id="test-004",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.ANALYTICS,
                data_class=DataClassification.UNKNOWN,
                owner="carol",
                source_system="worker-3",
                destination="analytics_warehouse",
            ),
            row_count=10,
        )
        assert self.validator.permit(manifest) is False

    def test_validate_with_incomplete_metadata_raises(self):
        """Incomplete metadata raises PurposeViolationError."""
        manifest = IngestionManifest(
            data_id="test-005",
            purpose_metadata=PurposeMetadata(
                purpose=None,
                data_class=DataClassification.OPERATIONAL,
                owner="alice",
                source_system="worker-1",
                destination="",
            ),
            row_count=100,
        )
        with pytest.raises(PurposeViolationError) as exc:
            self.validator.validate(manifest)
        assert "incomplete" in str(exc.value).lower()

    def test_validate_successful(self):
        """Valid manifest passes validation."""
        manifest = IngestionManifest(
            data_id="test-006",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.OPERATIONS,
                data_class=DataClassification.OPERATIONAL,
                owner="alice",
                source_system="worker-1",
                destination="target_db",
            ),
            row_count=500,
        )
        assert self.validator.validate(manifest) is True

    def test_audit_log_records_decisions(self):
        """Audit log records both permitted and blocked writes."""
        valid = IngestionManifest(
            data_id="audit-01",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.OPERATIONS,
                data_class=DataClassification.OPERATIONAL,
                owner="alice",
                source_system="worker-1",
                destination="target_db",
            ),
            row_count=100,
        )
        invalid = IngestionManifest(
            data_id="audit-02",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.MARKETING,
                data_class=DataClassification.PII,
                owner="alice",
                source_system="worker-1",
                destination="public_cache",
            ),
            row_count=100,
        )
        self.validator.permit(valid)
        self.validator.permit(invalid)

        log = self.validator.get_audit_log()
        assert len(log) == 2
        assert log[0]["action"] == "permitted"
        assert log[1]["action"] == "blocked"
        assert log[1]["reason"] != ""

    def test_report_by_purpose_and_owner(self):
        """Report groups writes by purpose and owner."""
        ops = IngestionManifest(
            data_id="rpt-01",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.OPERATIONS,
                data_class=DataClassification.OPERATIONAL,
                owner="alice",
                source_system="w1",
                destination="db1",
            ),
            row_count=10,
        )
        analytics = IngestionManifest(
            data_id="rpt-02",
            purpose_metadata=PurposeMetadata(
                purpose=PurposeCategory.ANALYTICS,
                data_class=DataClassification.ANALYTICAL,
                owner="bob",
                source_system="w2",
                destination="analytics_warehouse",
            ),
            row_count=20,
        )
        self.validator.permit(ops)
        self.validator.permit(analytics)
        report = self.validator.get_report()
        assert "operations" in report["by_purpose"]
        assert "analytics" in report["by_purpose"]
        assert "alice" in report["by_owner"]
        assert "bob" in report["by_owner"]


class TestDataClassificationEnum:

    def test_all_classifications(self):
        """All expected classifications exist."""
        assert DataClassification.PII.value == "pii"
        assert DataClassification.FINANCIAL.value == "financial"
        assert DataClassification.OPERATIONAL.value == "operational"
        assert DataClassification.ANALYTICAL.value == "analytical"
        assert DataClassification.SYSTEM.value == "system"

    def test_all_purposes(self):
        """All expected purpose categories exist."""
        assert PurposeCategory.AUDIT.value == "audit"
        assert PurposeCategory.BILLING.value == "billing"
        assert PurposeCategory.OPERATIONS.value == "operations"
        assert PurposeCategory.ANALYTICS.value == "analytics"
        assert PurposeCategory.COMPLIANCE.value == "compliance"
        assert PurposeCategory.PRODUCT_IMPROVEMENT.value == "product_improvement"