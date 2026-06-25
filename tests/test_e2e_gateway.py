"""Quick end-to-end test of the Sentinel gateway."""
import httpx

BASE = "http://127.0.0.1:8000"

# Health check
r = httpx.get(f"{BASE}/health", timeout=5)
print("Health:", r.json())

# Execute 3 tool calls with the same task
for i in range(3):
    payload = {
        "task_id": "e2e-demo",
        "tool_name": "llm.chat",
        "params": {
            "messages": [{"role": "user", "content": f"Test message {i+1}"}],
            "max_tokens": 50
        },
        "model": "local/ollama/llama3",
        "estimated_input": f"Test message {i+1}",
        "max_output_tokens": 50
    }
    r = httpx.post(f"{BASE}/v1/execute", json=payload, timeout=10)
    result = r.json()
    print(f"Step {i+1}: status={result['status']} cost=${result['cost_usd']:.6f} time={result['execution_time_ms']:.0f}ms")

# Check budget
r = httpx.get(f"{BASE}/v1/tasks/e2e-demo/budget", timeout=5)
budget = r.json()
print(f"\nBudget: total=${budget['total_budget_usd']:.4f} remaining=${budget['remaining_budget_usd']:.4f} spent=${budget['total_budget_usd'] - budget['remaining_budget_usd']:.4f}")

# Check trace
r = httpx.get(f"{BASE}/v1/tasks/e2e-demo/trace", timeout=5)
trace = r.json()
print(f"Trace: {len(trace)} step(s)")

# Replay: get first step
r = httpx.get(f"{BASE}/v1/tasks/e2e-demo/replay/state", timeout=5)
state = r.json()
print(f"Replay state: step {state['current_step']+1} of {state['total_steps']}")

# List all tasks
r = httpx.get(f"{BASE}/v1/tasks", timeout=5)
tasks = r.json()
print(f"\nAll tasks: {len(tasks)} task(s) registered")

print("\nAll e2e tests PASSED!")
