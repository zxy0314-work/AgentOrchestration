"""Anonymization validator — enforces minimum aggregation thresholds.

Issue #4194 — Analytics publication enforces anonymization and minimum
group-size rules at publish time, blocking groups below configurable threshold.
"""

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


class AnonymizationThresholdError(Exception):
    """Raised when a metric group is below the anonymity threshold."""
    def __init__(self, group_key: str, group_size: int, threshold: int):
        self.group_key = group_key
        self.group_size = group_size
        self.threshold = threshold
        super().__init__(
            f"Group '{group_key}' (size={group_size}) below threshold={threshold}"
        )


@dataclass
class MetricGroup:
    """A group of aggregated metric data to be published."""
    key: str
    size: int  # number of entities contributing to this group
    metrics: Dict[str, Any]
    source: str
    tags: Dict[str, str] = field(default_factory=dict)

    def is_anonymized(self) -> bool:
        """A group is safely anonymized if its size exceeds threshold."""
        return self.size >= MINIMUM_ANONYMITY_THRESHOLD


# Default minimum group size for anonymization (k-anonymity standard)
MINIMUM_ANONYMITY_THRESHOLD = 5


class AnonymizationValidator:
    """Validates metric groups before analytics publication.

    Enforces minimum aggregation thresholds to prevent re-identification
    via small group sizes. Suppressed groups are reported without exposing
    their contents.
    """

    def __init__(self, threshold: int = MINIMUM_ANONYMITY_THRESHOLD):
        self.threshold = threshold
        self._suppressed_groups: List[Dict] = []
        self._published_groups: List[Dict] = []

    def validate_group(self, group: MetricGroup) -> bool:
        """Validate a single metric group.

        Returns True if the group meets the anonymity threshold.
        Raises AnonymizationThresholdError if below threshold.
        """
        if group.size < self.threshold:
            raise AnonymizationThresholdError(group.key, group.size, self.threshold)
        return True

    def publish(self, groups: List[MetricGroup]) -> Dict[str, Any]:
        """Publish metric groups, enforcing anonymization.

        Returns a report with:
        - published: list of published group metadata (no metrics for small groups)
        - suppressed: list of suppressed group keys
        - published_count: number of groups published
        - suppressed_count: number of groups suppressed
        - threshold: the configured threshold
        """
        published = []
        suppressed = []

        for group in groups:
            try:
                self.validate_group(group)
                # Publish metadata without exposing sensitive details
                published.append({
                    "key": group.key,
                    "source": group.source,
                    "size": group.size,
                    "tags": group.tags,
                    "timestamp": time.time(),
                })
                self._published_groups.append(published[-1])
            except AnonymizationThresholdError as e:
                # Suppress group — report existence WITHOUT exposing contents
                suppressed.append({
                    "key": group.key,
                    "reason": f"below_threshold (size={group.size}, threshold={self.threshold})",
                    "timestamp": time.time(),
                })
                self._suppressed_groups.append(suppressed[-1])

        return {
            "published": published,
            "suppressed": suppressed,
            "published_count": len(published),
            "suppressed_count": len(suppressed),
            "threshold": self.threshold,
        }

    def get_suppressed_groups(self) -> List[Dict]:
        """Get list of suppressed groups with reasons (no metrics exposed)."""
        return list(self._suppressed_groups)

    def get_published_groups(self) -> List[Dict]:
        """Get list of published groups with metadata."""
        return list(self._published_groups)