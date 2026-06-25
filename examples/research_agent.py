"""
Research Agent - Demo script showing a 3-step agent using AgentFence.

This script simulates a research agent that:
1. Searches for information on a topic.
2. Summarizes the search results.
3. Writes a final report.

It uses the local/ollama/llama3 model which has $0 cost in pricing.json,
so the budget never depletes. Combined with AGENTFENCE_MOCK_MODE=1 (or no
API key), this runs completely offline with no external API calls.

Usage:
    cd C:\\Users\\rehan\\agentfence
    python -m examples.research_agent

Make sure the gateway is running:
    python -m uvicorn agentfence.gateway:app --port 8000
"""

from __future__ import annotations

import json
import os
import httpx
import uuid

# Set mock mode so the gateway returns fake responses without any API key
os.environ.setdefault("AGENTFENCE_MOCK_MODE", "1")

GATEWAY_URL = "http://localhost:8000"
TASK_ID = f"research-{uuid.uuid4().hex[:8]}"
BUDGET_USD = 0.50
MODEL = "local/ollama/llama3"


def execute_tool(
    client: httpx.Client,
    tool_name: str,
    params: dict,
    estimated_input: str,
    max_output_tokens: int = 500,
) -> dict:
    """
    Send a tool execution request to the AgentFence gateway.

    Args:
        client: The httpx client.
        tool_name: Name of the tool to call.
        params: Tool parameters.
        estimated_input: Input text for cost estimation.
        max_output_tokens: Expected max output tokens.

    Returns:
        The gateway's JSON response.
    """
    payload = {
        "task_id": TASK_ID,
        "tool_name": tool_name,
        "params": params,
        "model": MODEL,
        "estimated_input": estimated_input,
        "max_output_tokens": max_output_tokens,
    }
    response = client.post(f"{GATEWAY_URL}/v1/execute", json=payload, timeout=60.0)
    response.raise_for_status()
    return response.json()


def get_remaining_budget(client: httpx.Client) -> float:
    """Fetch the remaining budget for the current task."""
    resp = client.get(f"{GATEWAY_URL}/v1/tasks/{TASK_ID}/budget")
    if resp.status_code == 200:
        return resp.json().get("remaining_budget_usd", 0.0)
    return 0.0


def main() -> None:
    """Run the multi-step research agent."""
    print("=" * 60)
    print("🔬 Research Agent - AgentFence Demo (Offline Mode)")
    print("=" * 60)
    print(f"Task ID : {TASK_ID}")
    print(f"Budget  : ${BUDGET_USD:.2f}")
    print(f"Model   : {MODEL}")
    print(f"Mode    : Mock (no API key needed)")
    print("-" * 60)

    steps = [
        {
            "label": "\U0001f4e1 Step 1: Searching for information...",
            "tool_name": "web_search",
            "params": {
                "query": "latest developments in AI agent security 2025",
                "max_results": 5,
            },
            "estimated_input": "Search for: latest developments in AI agent security 2025",
            "max_output_tokens": 300,
        },
        {
            "label": "\n\U0001f4dd Step 2: Summarizing search results...",
            "tool_name": "llm.chat",
            "params": {
                "messages": [
                    {
                        "role": "user",
                        "content": "Summarize the key findings about AI agent security "
                        "from these search results: [search results placeholder]. "
                        "Focus on cost control, prompt injection, and tool misuse.",
                    }
                ],
                "max_tokens": 400,
            },
            "estimated_input": "Summarize the key findings about AI agent security. "
            "Focus on cost control, prompt injection, and tool misuse.",
            "max_output_tokens": 400,
        },
        {
            "label": "\n\U0001f4c4 Step 3: Writing final report...",
            "tool_name": "llm.chat",
            "params": {
                "messages": [
                    {
                        "role": "user",
                        "content": "Write a concise 200-word report on AI agent security "
                        "best practices for startup engineering teams. "
                        "Include: cost monitoring, execution tracing, and failure replay.",
                    }
                ],
                "max_tokens": 500,
            },
            "estimated_input": "Write a concise 200-word report on AI agent security "
            "best practices for startup engineering teams.",
            "max_output_tokens": 500,
        },
    ]

    with httpx.Client() as client:
        for step_config in steps:
            print(step_config["label"])
            result = execute_tool(
                client,
                tool_name=step_config["tool_name"],
                params=step_config["params"],
                estimated_input=step_config["estimated_input"],
                max_output_tokens=step_config["max_output_tokens"],
            )
            print(f"   Status   : {result['status']}")
            print(f"   Cost     : ${result['cost_usd']:.6f}")
            print(f"   Time     : {result['execution_time_ms']:.0f}ms")

            remaining = get_remaining_budget(client)
            print(f"   Budget   : ${remaining:.4f} remaining of ${BUDGET_USD:.2f}")

            if result.get("output"):
                print(f"   Output   : {str(result['output'])[:200]}...")

            if result["status"] in ("budget_exceeded", "circuit_breaker"):
                print("\n🛑 Budget limit reached. Stopping.")
                break

        # Final Summary
        print("\n" + "=" * 60)
        print("📊 Final Summary")
        print("=" * 60)

        budget_resp = client.get(f"{GATEWAY_URL}/v1/tasks/{TASK_ID}/budget")
        if budget_resp.status_code == 200:
            budget = budget_resp.json()
            print(f"Total Budget : ${budget['total_budget_usd']:.4f}")
            print(f"Remaining    : ${budget['remaining_budget_usd']:.4f}")
            spent = budget['total_budget_usd'] - budget['remaining_budget_usd']
            print(f"Spent        : ${spent:.4f}")

        # Fetch and print the full trace
        trace_resp = client.get(f"{GATEWAY_URL}/v1/tasks/{TASK_ID}/trace")
        if trace_resp.status_code == 200:
            trace = trace_resp.json()
            print(f"Trace Steps  : {len(trace)}")
            print("\n📋 Full Trace:")
            print("-" * 60)
            for i, step in enumerate(trace, 1):
                print(f"  Step {i}: {step['tool_name']} | {step['model']} | "
                      f"status={step['status']} | cost=${step['cost_usd']:.6f} | "
                      f"latency={step['latency_ms']:.0f}ms")
                if step.get("output_preview"):
                    print(f"    Output: {step['output_preview'][:120]}")
                if step.get("error"):
                    print(f"    Error : {step['error'][:120]}")

        print(f"\n✅ Research agent completed successfully!")
        print(f"   All calls were handled in mock mode — no API key needed.")
        print(f"   View the dashboard: streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
