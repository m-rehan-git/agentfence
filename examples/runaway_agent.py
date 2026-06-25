"""
Runaway Agent - Demo script showing Sentinel tracking a looping agent.

This script demonstrates Sentinel's execution tracing by:
1. Setting a $0.20 budget.
2. Using the local/ollama/llama3 model (free tier, $0 cost per call).
3. Running a loop of 5 tool calls that all succeed (since model is free).
4. Printing the full trace at the end.

Since the local model has $0 cost in pricing.json, the budget never depletes
and all calls succeed. The "runaway" aspect is that the agent keeps calling
in a loop, but Sentinel tracks every step. With a paid model (e.g.,
gpt-3.5-turbo), the budget would be enforced and the circuit breaker would
trip once funds are exhausted.

Usage:
    cd C:\\Users\\rehan\\sentinel
    python -m examples.runaway_agent

Make sure the gateway is running:
    python -m uvicorn sentinel.gateway:app --port 8000
"""

from __future__ import annotations

import json
import os
import httpx
import uuid

# Set mock mode so the gateway returns fake responses without any API key
os.environ.setdefault("AGENTFENCE_MOCK_MODE", "1")

GATEWAY_URL = "http://localhost:8000"
TASK_ID = f"runaway-{uuid.uuid4().hex[:8]}"
BUDGET_USD = 0.20
MODEL = "local/ollama/llama3"


def execute_tool(
    client: httpx.Client,
    tool_name: str,
    params: dict,
    estimated_input: str,
    max_output_tokens: int = 500,
) -> dict:
    """
    Send a tool execution request to the Sentinel gateway.

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
    """Run the runaway agent loop and show Sentinel tracking."""
    print("=" * 60)
    print("🔥 Runaway Agent - Sentinel Tracking Demo (Offline)")
    print("=" * 60)
    print(f"Task ID : {TASK_ID}")
    print(f"Budget  : ${BUDGET_USD:.2f}")
    print(f"Model   : {MODEL} (free tier - $0 cost)")
    print(f"Mode    : Mock (no API key needed)")
    print("-" * 60)
    print()
    print("NOTE: This agent loops 5 times. Since the model is free,")
    print("all calls succeed and the budget is never depleted.")
    print("With a paid model, Sentinel would enforce the budget")
    print("and trip the circuit breaker once funds run out.")
    print()

    total_cost = 0.0
    iteration = 0
    max_iterations = 5

    with httpx.Client() as client:
        while iteration < max_iterations:
            iteration += 1
            print(f"🔄 Iteration {iteration}/{max_iterations}...")

            prompt = (
                f"This is iteration {iteration} of a research task. "
                f"Provide a brief summary of AI agent safety best practices."
            )

            result = execute_tool(
                client,
                tool_name="llm.chat",
                params={
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                },
                estimated_input=prompt,
                max_output_tokens=300,
            )

            total_cost += result.get("cost_usd", 0.0)
            remaining = get_remaining_budget(client)

            print(f"   Status   : {result['status']}")
            print(f"   Cost     : ${result['cost_usd']:.6f}")
            print(f"   Total    : ${total_cost:.6f}")
            print(f"   Budget   : ${remaining:.4f} remaining of ${BUDGET_USD:.2f}")
            print(f"   Time     : {result['execution_time_ms']:.0f}ms")

            if result["status"] in ("budget_exceeded", "circuit_breaker"):
                print(f"\n🛑 Sentinel stopped the agent: {result['status']}")
                break

            if result["status"] == "error":
                print(f"\n⚠️  Error: {result.get('output', 'Unknown error')}")
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
            print(f"Tripped      : {'Yes ⚡' if budget.get('circuit_breaker_tripped') else 'No'}")

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

        # Explanation
        print("\n" + "=" * 60)
        print("💡 How the Circuit Breaker Works with Paid Models")
        print("=" * 60)
        print("""
This demo used 'local/ollama/llama3' which has $0 cost in pricing.json,
so all 5 iterations succeeded without depleting the $0.20 budget.

With a paid model (e.g., 'gpt-3.5-turbo' at $0.0005/1K input tokens):
  - Each call would cost ~$0.001-$0.01 depending on input size.
  - After ~20-50 calls, the $0.20 budget would be exhausted.
  - Sentinel would then:
      1. Reserve budget before each call (check_and_reserve).
      2. If estimated cost > remaining budget → 'budget_exceeded'.
      3. If actual cost pushes remaining below $0 → 'circuit_breaker'.
  - The agent would be stopped before racking up unexpected bills.

To try this with a paid model:
  1. Set AGENTFENCE_MOCK_MODE=0
  2. Set AGENTFENCE_API_KEY=***
  3. Change MODEL to 'gpt-3.5-turbo'
  4. Run this script again — the circuit breaker will trip!
""")

    print(f"\n✅ Runaway agent demo complete!")
    print(f"   View the dashboard: streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
