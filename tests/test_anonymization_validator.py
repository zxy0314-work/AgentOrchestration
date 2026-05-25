"""Tests for anonymization validation (issue #4194)."""

import pytest
from src.data.anonymization_validator import (
    MetricGroup,
    AnonymizationValidator,
    AnonymizationThresholdError,
    MINIMUM_ANONYMITY_THRESHOLD,
)


class TestAnonymizationValidator:

    def setup_method(self):
        self.validator = AnonymizationValidator(threshold=5)

    def test_large_group_publishes(self):
        """Group above threshold publishes successfully."""
        group = MetricGroup(key="ws-1", size=100, metrics={"avg_duration": 42.5}, source="worker")
        result = self.validator.publish([group])
        assert result["published_count"] == 1
        assert result["suppressed_count"] == 0
        assert result["published"][0]["key"] == "ws-1"

    def test_small_group_suppressed(self):
        """Group below threshold is suppressed."""
        group = MetricGroup(key="ws-2", size=3, metrics={"avg_duration": 12.3}, source="worker")
        result = self.validator.publish([group])
        assert result["published_count"] == 0
        assert result["suppressed_count"] == 1
        assert result["suppressed"][0]["key"] == "ws-2"
        assert "below_threshold" in result["suppressed"][0]["reason"]

    def test_boundary_group_at_threshold(self):
        """Group at exact threshold is published (>= semantics)."""
        group = MetricGroup(key="ws-3", size=5, metrics={"count": 100}, source="worker")
        result = self.validator.publish([group])
        assert result["published_count"] == 1
        assert result["suppressed_count"] == 0

    def test_mixed_groups(self):
        """Mixed large and small groups handles both correctly."""
        large = MetricGroup(key="large", size=200, metrics={"x": 1}, source="worker")
        small = MetricGroup(key="small", size=2, metrics={"x": 3}, source="worker")
        result = self.validator.publish([large, small])
        assert result["published_count"] == 1
        assert result["suppressed_count"] == 1
        assert result["published"][0]["key"] == "large"
        assert result["suppressed"][0]["key"] == "small"

    def test_suppressed_metadata_does_not_expose_metrics(self):
        """Suppressed groups report existence without exposing metrics."""
        group = MetricGroup(key="secret", size=2, metrics={"salary_avg": 99999, "names": "..."}, source="worker")
        result = self.validator.publish([group])
        assert result["suppressed"][0]["key"] == "secret"
        # Suppressed entries should NOT contain metrics
        assert "metrics" not in result["suppressed"][0]

    def test_validate_group_raises_below_threshold(self):
        """validate_group raises AnonymizationThresholdError for small groups."""
        group = MetricGroup(key="test", size=2, metrics={}, source="test")
        with pytest.raises(AnonymizationThresholdError) as exc:
            self.validator.validate_group(group)
        assert exc.value.group_size == 2
        assert exc.value.threshold == 5

    def test_custom_threshold(self):
        """Custom threshold can be set."""
        validator = AnonymizationValidator(threshold=10)
        group = MetricGroup(key="med", size=8, metrics={}, source="test")
        with pytest.raises(AnonymizationThresholdError):
            validator.validate_group(group)
        # With threshold=5 it would pass
        validator2 = AnonymizationValidator(threshold=5)
        assert validator2.validate_group(group) is True

    def test_empty_groups_list(self):
        """Empty groups list returns empty results."""
        result = self.validator.publish([])
        assert result["published_count"] == 0
        assert result["suppressed_count"] == 0
        assert result["published"] == []
        assert result["suppressed"] == []

    def test_get_suppressed_groups(self):
        """get_suppressed_groups returns accumulated suppressed entries."""
        self.validator.publish([MetricGroup(key="a", size=2, metrics={}, source="s")])
        self.validator.publish([MetricGroup(key="b", size=3, metrics={}, source="s")])
        suppressed = self.validator.get_suppressed_groups()
        assert len(suppressed) == 2

    def test_get_published_groups(self):
        """get_published_groups returns accumulated published entries."""
        self.validator.publish([MetricGroup(key="a", size=100, metrics={}, source="s")])
        self.validator.publish([MetricGroup(key="b", size=200, metrics={}, source="s")])
        published = self.validator.get_published_groups()
        assert len(published) == 2