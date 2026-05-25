"""Tests for namespace-aware metrics."""
import pytest
from src.common.metrics import MetricsCollector


class TestMetricsNamespace:
    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    def test_increment_without_namespace(self, metrics):
        metrics.increment("requests")
        snap = metrics.snapshot()
        assert snap["counters"]["requests"] == 1

    def test_increment_with_namespace(self, metrics):
        metrics.increment("requests", namespace="api")
        metrics.increment("requests", namespace="worker")
        snap = metrics.snapshot()
        assert snap["counters"]["api.requests"] == 1
        assert snap["counters"]["worker.requests"] == 1

    def test_gauge_with_namespace(self, metrics):
        metrics.gauge("cpu.percent", 75.0, namespace="worker-1")
        snap = metrics.snapshot()
        assert snap["gauges"]["worker-1.cpu.percent"] == 75.0

    def test_observe_with_namespace(self, metrics):
        metrics.observe("latency", 0.5, namespace="api")
        metrics.observe("latency", 0.7, namespace="api")
        snap = metrics.snapshot()
        assert snap["histograms"]["api.latency"]["count"] == 2

    def test_snapshot_filters_by_namespace(self, metrics):
        metrics.increment("calls", namespace="api")
        metrics.increment("calls", namespace="worker")
        metrics.increment("errors", namespace="api")
        api_snap = metrics.snapshot(namespace="api")
        assert "calls" in api_snap["counters"]
        assert "errors" in api_snap["counters"]
        assert len(api_snap["counters"]) == 2

    def test_snapshot_empty_namespace_returns_all(self, metrics):
        metrics.increment("global")
        metrics.increment("specific", namespace="ns")
        snap = metrics.snapshot()
        assert "global" in snap["counters"]
        assert "ns.specific" in snap["counters"]

    def test_namespace_isolation(self, metrics):
        metrics.increment("count", namespace="ns1")
        metrics.increment("count", namespace="ns2")
        ns1_snap = metrics.snapshot(namespace="ns1")
        assert "count" in ns1_snap["counters"]
        assert ns1_snap["counters"]["count"] == 1

    def test_timer_with_namespace(self, metrics):
        import time
        metrics.start_timer("process", namespace="job-1")
        time.sleep(0.01)
        duration = metrics.stop_timer("process", namespace="job-1")
        assert duration > 0
        snap = metrics.snapshot()
        assert "job-1.process" in snap["histograms"]

    def test_empty_namespace_filter_returns_empty(self, metrics):
        metrics.increment("calls", namespace="worker")
        snap = metrics.snapshot(namespace="nonexistent")
        assert snap["counters"] == {}
        assert snap["gauges"] == {}
        assert snap["histograms"] == {}

    def test_snapshot_preserves_metric_names(self, metrics):
        metrics.increment("task.duration", namespace="agent.my-agent-1")
        snap = metrics.snapshot(namespace="agent.my-agent-1")
        assert "task.duration" in snap["counters"]
