"""Run a full demo: start gateway, run example agents, verify results."""
import subprocess
import sys
import time
import os
import signal

PYTHON = r"C:\Users\rehan\AppData\Local\Programs\Python\Python314\python.exe"
PROJECT = r"C:\Users\rehan\sentinel"

# Start gateway in background
print("=" * 60)
print("Starting Sentinel Gateway...")
print("=" * 60)
gateway = subprocess.Popen(
    [PYTHON, "-m", "uvicorn", "sentinel.gateway:app", "--port", "8000", "--host", "127.0.0.1"],
    cwd=PROJECT,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
)

# Wait for gateway to be healthy
import httpx
for attempt in range(20):
    time.sleep(1)
    try:
        r = httpx.get("http://127.0.0.1:8000/health", timeout=2)
        if r.status_code == 200:
            print(f"Gateway healthy: {r.json()}")
            break
    except Exception:
        print(f"  Waiting for gateway... (attempt {attempt+1})")
else:
    print("Gateway failed to start!")
    gateway.kill()
    sys.exit(1)

# Run research agent
print("\n" + "=" * 60)
print("Running Research Agent...")
print("=" * 60)
os.environ["AGENTFENCE_MOCK_MODE"] = "1"
result = subprocess.run(
    [PYTHON, "-m", "examples.research_agent"],
    cwd=PROJECT,
    capture_output=True,
    text=True,
    timeout=60,
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:500])
print(f"Research agent exit code: {result.returncode}")

# Run runaway agent
print("\n" + "=" * 60)
print("Running Runaway Agent...")
print("=" * 60)
result = subprocess.run(
    [PYTHON, "-m", "examples.runaway_agent"],
    cwd=PROJECT,
    capture_output=True,
    text=True,
    timeout=60,
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:500])
print(f"Runaway agent exit code: {result.returncode}")

# Cleanup: stop gateway
print("\n" + "=" * 60)
print("Stopping Gateway...")
print("=" * 60)
gateway.terminate()
gateway.wait(timeout=5)
print("Gateway stopped.")
print("\nFull demo complete!")
