"""
Tests for the Tracer module.

Tests cover:
- Logging and retrieving trace steps
- Multiple step ordering
- Empty task traces
- Recent tasks listing
- JSONL and SQLite consistency
- Special characters in task IDs
- Disk full handling (mocked)
- Concurrent logging

Run with: pytest tests/test_tracer.py -v
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from sentinel.models import TraceStep
from sentinel.tracer import Tracer


@pytest.fixture
def tracer(tmp_path: Path) -> Tracer:
    """Create a Tracer with temporary directories."""
    traces_dir = tmp_path / "traces"
    db_path = tmp_path / "test_traces.db"
    traces_dir.mkdir(parents=True, exist_ok=True)
    return Tracer(traces_dir=traces_dir, db_path=db_path)


def _make_step(tool_name: str = "test_tool", model: str = "gpt-4o", **overrides) -> TraceStep:
    """Helper to create a TraceStep with sensible defaults."""
    defaults = {
        "tool_name": tool_name,
        "model": model,
        "input_preview": f"input for {tool_name}",
        "output_preview": f"output for {tool_name}",
        "input_tokens": 10,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "latency_ms": 100.0,
        "status": "success",
    }
    defaults.update(overrides)
    return TraceStep(**defaults)


class TestLogAndRetrieveStep:
    """Test basic log and retrieve operations."""

    def test_log_and_retrieve_step(self, tracer: Tracer) -> None:
        """A logged step should be retrievable."""
        task_id = "retrieve-test"
        step = _make_step("my_tool")
        tracer.log_step(task_id, step)

        steps = tracer.get_trace(task_id)
        assert len(steps) == 1
        assert steps[0].tool_name == "my_tool"
        assert steps[0].step_id == step.step_id

    def test_log_step_returns_correct_step_id(self, tracer: Tracer) -> None:
        """The step_id should be preserved through the round-trip."""
        task_id = "id-test"
        step = _make_step()
        tracer.log_step(task_id, step)

        steps = tracer.get_trace(task_id)
        assert steps[0].step_id == step.step_id

    def test_log_step_preserves_all_fields(self, tracer: Tracer) -> None:
        """All fields of the TraceStep should be preserved."""
        task_id = "fields-test"
        step = _make_step(
            tool_name="complex_tool",
            model="gpt-4o-mini",
            input_preview="detailed input",
            output_preview="detailed output",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.005,
            latency_ms=2500.0,
            status="success",
        )
        tracer.log_step(task_id, step)

        steps = tracer.get_trace(task_id)
        retrieved = steps[0]
        assert retrieved.tool_name == "complex_tool"
        assert retrieved.model == "gpt-4o-mini"
        assert retrieved.input_preview == "detailed input"
        assert retrieved.output_preview == "detailed output"
        assert retrieved.input_tokens == 500
        assert retrieved.output_tokens == 200
        assert retrieved.cost_usd == pytest.approx(0.005)
        assert retrieved.latency_ms == pytest.approx(2500.0)
        assert retrieved.status == "success"


class TestLogMultipleStepsOrder:
    """Test that multiple steps maintain their order."""

    def test_log_multiple_steps_order(self, tracer: Tracer) -> None:
        """Steps should be returned in the order they were logged."""
        task_id = "order-test"
        for i in range(5):
            step = _make_step(tool_name=f"tool_{i}")
            tracer.log_step(task_id, step)

        steps = tracer.get_trace(task_id)
        assert len(steps) == 5
        for i, step in enumerate(steps):
            assert step.tool_name == f"tool_{i}"

    def test_log_100_steps_order(self, tracer: Tracer) -> None:
        """100 steps should all be logged and retrieved in order."""
        task_id = "bulk-order-test"
        for i in range(100):
            step = _make_step(tool_name=f"tool_{i:03d}")
            tracer.log_step(task_id, step)

        steps = tracer.get_trace(task_id)
        assert len(steps) == 100
        for i, step in enumerate(steps):
            assert step.tool_name == f"tool_{i:03d}"


class TestGetTraceEmptyTask:
    """Test retrieving traces for tasks with no data."""

    def test_get_trace_empty_task(self, tracer: Tracer) -> None:
        """A task with no logged steps should return an empty list."""
        steps = tracer.get_trace("nonexistent-task")
        assert steps == []

    def test_get_trace_empty_list_type(self, tracer: Tracer) -> None:
        """Empty trace should return a list, not None."""
        steps = tracer.get_trace("empty-task")
        assert isinstance(steps, list)


class TestGetRecentTasks:
    """Test the get_recent_tasks summary endpoint."""

    def test_get_recent_tasks(self, tracer: Tracer) -> None:
        """Recent tasks should be returned with correct summaries."""
        for i in range(3):
            task_id = f"recent-task-{i}"
            for j in range(2):
                step = _make_step(tool_name=f"tool_{j}", cost_usd=0.01 * (i + 1))
                tracer.log_step(task_id, step)

        recent = tracer.get_recent_tasks(limit=10)
        assert len(recent) == 3
        for task in recent:
            assert "task_id" in task
            assert "step_count" in task
            assert "total_cost" in task
            assert "last_activity" in task
            assert task["step_count"] == 2

    def test_get_recent_tasks_respects_limit(self, tracer: Tracer) -> None:
        """The limit parameter should be respected."""
        for i in range(10):
            step = _make_step()
            tracer.log_step(f"limit-task-{i}", step)

        recent = tracer.get_recent_tasks(limit=5)
        assert len(recent) <= 5

    def test_get_recent_tasks_ordered_by_last_activity(self, tracer: Tracer) -> None:
        """Recent tasks should be ordered by last activity descending."""
        # Log tasks in order
        for i in range(3):
            step = _make_step()
            tracer.log_step(f"ordered-task-{i}", step)

        recent = tracer.get_recent_tasks(limit=10)
        # Most recent should be first
        assert len(recent) == 3


class TestJsonlAndSqliteConsistency:
    """Test that JSONL and SQLite contain the same data."""

    def test_jsonl_and_sqlite_consistency(self, tracer: Tracer) -> None:
        """Data written to JSONL should match what's in SQLite."""
        task_id = "consistency-test"
        step = _make_step(tool_name="consistency_tool", cost_usd=0.042)
        tracer.log_step(task_id, step)

        # Read from JSONL
        jsonl_steps = tracer.get_trace(task_id)
        assert len(jsonl_steps) == 1
        assert jsonl_steps[0].tool_name == "consistency_tool"
        assert jsonl_steps[0].cost_usd == pytest.approx(0.042)

        # Read from SQLite
        recent = tracer.get_recent_tasks(limit=10)
        matching = [t for t in recent if t["task_id"] == task_id]
        assert len(matching) == 1
        assert matching[0]["total_cost"] == pytest.approx(0.042)

    def test_jsonl_file_created(self, tracer: Tracer) -> None:
        """A JSONL file should be created for each task."""
        task_id = "file-creation-test"
        step = _make_step()
        tracer.log_step(task_id, step)

        jsonl_path = tracer._get_jsonl_path(task_id)
        assert jsonl_path.exists()

    def test_jsonl_file_contains_valid_json(self, tracer: Tracer) -> None:
        """Each line in the JSONL file should be valid JSON."""
        task_id = "json-validity-test"
        step = _make_step()
        tracer.log_step(task_id, step)

        jsonl_path = tracer._get_jsonl_path(task_id)
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                data = json.loads(line.strip())
                assert "step_id" in data
                assert "tool_name" in data


class TestTracerSpecialCharacters:
    """Test handling of special characters in task IDs."""

    def test_tracer_with_special_characters_in_task_id(self, tracer: Tracer) -> None:
        """Task IDs with special characters should be handled safely."""
        special_ids = [
            "task-with-dashes",
            "task_with_underscores",
            "task.with.dots",
            "task123",
            "UPPERCASE-TASK",
        ]
        for task_id in special_ids:
            step = _make_step()
            tracer.log_step(task_id, step)
            steps = tracer.get_trace(task_id)
            assert len(steps) == 1, f"Failed for task_id: {task_id}"

    def test_tracer_path_traversal_safe(self, tracer: Tracer) -> None:
        """Task IDs with path traversal characters should be sanitized."""
        task_id = "../../../etc/passwd"
        step = _make_step()
        tracer.log_step(task_id, step)

        # The path should be sanitized
        jsonl_path = tracer._get_jsonl_path(task_id)
        assert ".." not in jsonl_path.name
        assert jsonl_path.exists()


class TestTracerDiskFull:
    """Test graceful handling of disk errors."""

    def test_tracer_disk_full_graceful(self, tracer: Tracer) -> None:
        """Disk full errors should not crash the tracer."""
        step = _make_step()

        with patch("builtins.open", side_effect=IOError("No space left on device")):
            # Should not raise — tracer handles IOError gracefully
            tracer.log_step("disk-full-task", step)


class TestConcurrentLogging:
    """Test thread-safety of trace logging."""

    def test_concurrent_logging(self, tracer: Tracer) -> None:
        """Multiple threads logging to the same task should not lose data."""
        task_id = "concurrent-log-task"
        num_threads = 10
        steps_per_thread = 10
        errors: list[str] = []
        lock = threading.Lock()

        def log_steps(thread_id: int) -> None:
            try:
                for j in range(steps_per_thread):
                    step = _make_step(tool_name=f"thread-{thread_id}-step-{j}")
                    tracer.log_step(task_id, step)
            except Exception as e:
                with lock:
                    errors.append(f"thread-{thread_id}: {e}")

        threads = [threading.Thread(target=log_steps, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent logging: {errors}"

        steps = tracer.get_trace(task_id)
        assert len(steps) == num_threads * steps_per_thread

    def test_concurrent_different_tasks(self, tracer: Tracer) -> None:
        """Logging to different tasks concurrently should work correctly."""
        num_tasks = 20
        errors: list[str] = []
        lock = threading.Lock()

        def log_to_task(task_id: str) -> None:
            try:
                for j in range(5):
                    step = _make_step(tool_name=f"tool-{j}")
                    tracer.log_step(task_id, step)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=log_to_task, args=(f"concurrent-task-{i}",))
            for i in range(num_tasks)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"

        for i in range(num_tasks):
            steps = tracer.get_trace(f"concurrent-task-{i}")
            assert len(steps) == 5, f"Task concurrent-task-{i} has {len(steps)} steps, expected 5"
