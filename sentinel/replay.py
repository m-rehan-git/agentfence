"""
Replay Engine - Production-grade trace replay state machine.

Loads a trace from JSONL into memory and maintains a cursor that can
be advanced, rewound, or jumped to any position.

Features:
  - Search steps by tool_name or status
  - Reset to beginning
  - Structured logging
"""

from __future__ import annotations

import logging
from typing import Optional

from sentinel.models import TraceStep
from sentinel.tracer import Tracer

logger = logging.getLogger(__name__)


class ReplayEngine:
    """State machine for replaying execution traces."""

    def __init__(self, tracer: Optional[Tracer] = None):
        """
        Initialize the ReplayEngine.

        Args:
            tracer: Tracer instance to load traces from. Creates a default one if None.
        """
        self._tracer = tracer or Tracer()
        self._task_id: Optional[str] = None
        self._steps: list[TraceStep] = []
        self._current_index: int = 0

    def load_trace(self, task_id: str) -> int:
        """
        Load a trace from JSONL into memory for replay.

        Args:
            task_id: The task whose trace to load.

        Returns:
            Number of steps loaded.

        Raises:
            FileNotFoundError: If no trace file exists for the task.
        """
        self._task_id = task_id
        self._steps = self._tracer.get_trace(task_id)
        self._current_index = 0

        if not self._steps:
            raise FileNotFoundError(
                f"No trace found for task '{task_id}'. "
                "Make sure the task has been executed and logged."
            )

        logger.info(
            "Trace loaded",
            extra={"task_id": task_id, "steps": len(self._steps)},
        )
        return len(self._steps)

    def get_total_steps(self) -> int:
        """Get the total number of steps in the loaded trace."""
        return len(self._steps)

    def get_step(self, index: int) -> TraceStep:
        """
        Get a specific step by index.

        Args:
            index: Zero-based step index.

        Raises:
            IndexError: If the index is out of bounds.
        """
        if index < 0 or index >= len(self._steps):
            raise IndexError(
                f"Step index {index} out of range [0, {len(self._steps) - 1}]"
            )
        return self._steps[index]

    def get_state(self) -> dict:
        """
        Get the current replay state.

        Returns:
            Dict with task_id, current_step, total_steps, at_start, at_end.
        """
        total = len(self._steps)
        return {
            "task_id": self._task_id,
            "current_step": self._current_index,
            "total_steps": total,
            "at_start": self._current_index == 0,
            "at_end": self._current_index >= total - 1 if total > 0 else True,
        }

    def next_step(self) -> Optional[TraceStep]:
        """Advance to the next step and return it. Returns None if at end."""
        if self._current_index < len(self._steps) - 1:
            self._current_index += 1
            return self._steps[self._current_index]
        return None

    def prev_step(self) -> Optional[TraceStep]:
        """Rewind to the previous step and return it. Returns None if at start."""
        if self._current_index > 0:
            self._current_index -= 1
            return self._steps[self._current_index]
        return None

    def jump_to(self, index: int) -> TraceStep:
        """
        Jump directly to a specific step index.

        Args:
            index: Zero-based step index.

        Raises:
            IndexError: If the index is out of bounds.
        """
        if index < 0 or index >= len(self._steps):
            raise IndexError(
                f"Step index {index} out of range [0, {len(self._steps) - 1}]"
            )
        self._current_index = index
        return self._steps[self._current_index]

    def reset(self) -> None:
        """Reset the replay cursor to the beginning."""
        self._current_index = 0

    def search(
        self,
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[tuple[int, TraceStep]]:
        """
        Search for steps matching criteria.

        Args:
            tool_name: Filter by tool name (exact match).
            status: Filter by status (exact match).

        Returns:
            List of (index, TraceStep) tuples matching the criteria.
        """
        results: list[tuple[int, TraceStep]] = []
        for i, step in enumerate(self._steps):
            if tool_name is not None and step.tool_name != tool_name:
                continue
            if status is not None and step.status != status:
                continue
            results.append((i, step))
        return results

    def get_current_step(self) -> Optional[TraceStep]:
        """Get the step at the current cursor position."""
        if not self._steps:
            return None
        return self._steps[self._current_index]
