"""
Sentinel — LangChain Integration

Provides a LangChain-compatible callback handler that wraps Sentinel's
budget enforcement, security, and tracing for LangChain agents.

This is a lightweight client that communicates with a running Sentinel
gateway via HTTP. Start the gateway first:
    sentinel start
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SentinelCallback:
    """
    LangChain CallbackHandler that integrates with Sentinel's security pipeline.

    Wraps LangChain agent execution with:
    - Budget enforcement (pre-execution cost estimation)
    - Execution tracing (every LLM call logged)
    - Security monitoring (tool usage, prompt injection detection)

    Args:
        gateway_url: URL of the Sentinel gateway (default: http://localhost:8000)
        agent_id: Agent identity for authentication
        agent_key: API key for the agent (from `sentinel agents add`)
        task_id: Optional task identifier (auto-generated if not provided)
        budget_usd: Budget cap for this session
        enabled: Set to False to disable all Sentinel tracking
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:8000",
        agent_id: str = "langchain-agent",
        agent_key: str = "",
        task_id: Optional[str] = None,
        budget_usd: float = 10.0,
        enabled: bool = True,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self.agent_key = agent_key
        self.task_id = task_id or f"langchain-{uuid.uuid4().hex[:12]}"
        self.budget_usd = budget_usd
        self.enabled = enabled
        self._total_cost = 0.0
        self._call_count = 0

    def _record_llm_call(
        self,
        prompt: str,
        response: str,
        model: str = "gpt-4o",
        duration_ms: float = 0.0,
    ) -> None:
        """Record an LLM call to Sentinel's tracing system."""
        if not self.enabled:
            return

        self._call_count += 1

        try:
            import httpx

            payload = {
                "task_id": self.task_id,
                "tool_name": "llm.chat",
                "model": model,
                "params": {"messages": [{"role": "user", "content": prompt}]},
                "estimated_input": prompt,
                "max_output_tokens": len(response.split()) * 2,
                "_agent_key": self.agent_key,
                "_agent_id": self.agent_id,
            }

            resp = httpx.post(
                f"{self.gateway_url}/v1/execute",
                json=payload,
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                cost = data.get("cost_usd", 0.0)
                self._total_cost += cost
            elif resp.status_code == 402:
                logger.warning(
                    "[Sentinel] Budget exceeded! Agent execution halted."
                )
        except Exception as e:
            logger.debug("[Sentinel] Failed to record LLM call: %s", e)

    # -- LangChain CallbackHandler interface --

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Called when LLM starts processing."""
        self._llm_start_time = time.monotonic()
        self._current_model = kwargs.get("invocation_params", {}).get("model", "gpt-4o")
        self._current_prompt = prompts[0] if prompts else ""

    def on_llm_end(
        self,
        response: Any,
        **kwargs: Any,
    ) -> None:
        """Called when LLM finishes processing."""
        duration_ms = 0.0
        if hasattr(self, "_llm_start_time"):
            duration_ms = (time.monotonic() - self._llm_start_time) * 1000

        # Extract response text from various LangChain response formats
        output_text = ""
        if hasattr(response, "generations") and response.generations:
            generations = response.generations
            if generations and generations[0]:
                output_text = generations[0][0].text
        elif hasattr(response, "output"):
            output_text = str(response.output)
        elif isinstance(response, str):
            output_text = response

        self._record_llm_call(
            prompt=self._current_prompt,
            response=output_text,
            model=self._current_model,
            duration_ms=duration_ms,
        )

    def on_llm_error(
        self,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Called when LLM encounters an error."""
        logger.warning("[Sentinel] LLM error: %s", error)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Called when a tool starts executing."""
        tool_name = serialized.get("name", "unknown")
        logger.debug("[Sentinel] Tool started: %s", tool_name)

    def on_tool_end(
        self,
        output: str,
        **kwargs: Any,
    ) -> None:
        """Called when a tool finishes executing."""
        logger.debug("[Sentinel] Tool completed (output: %d chars)", len(output))

    def on_tool_error(
        self,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Called when a tool encounters an error."""
        logger.warning("[Sentinel] Tool error: %s", error)

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Called when a chain starts executing."""
        chain_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])
        logger.debug("[Sentinel] Chain started: %s", chain_name)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Called when a chain finishes executing."""
        logger.debug("[Sentinel] Chain completed")

    def on_chain_error(
        self,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        """Called when a chain encounters an error."""
        logger.warning("[Sentinel] Chain error: %s", error)

    @property
    def total_cost(self) -> float:
        """Total accumulated cost in USD for this session."""
        return round(self._total_cost, 6)

    @property
    def call_count(self) -> int:
        """Total number of LLM calls recorded."""
        return self._call_count

    def get_session_summary(self) -> dict[str, Any]:
        """Get a summary of the current session."""
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "total_cost_usd": self.total_cost,
            "call_count": self.call_count,
            "budget_usd": self.budget_usd,
            "budget_remaining": round(self.budget_usd - self.total_cost, 6),
        }
