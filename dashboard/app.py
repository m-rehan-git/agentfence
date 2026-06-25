"""
Sentinel Dashboard - Streamlit single-page application.

This dashboard provides a visual interface for:
- Viewing all tasks and their budget status.
- Inspecting execution traces step-by-step.
- Replaying traces with next/previous/jump controls.
- Visualizing cumulative cost burn-over-time.

Run with: streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pandas as pd
import streamlit as st

API_BASE = st.sidebar.text_input("Gateway URL", value="http://localhost:8000")


def api_get(path: str) -> Optional[Any]:
    """Make a GET request to the Sentinel gateway."""
    try:
        response = httpx.get(f"{API_BASE}{path}", timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, json_body: Optional[dict] = None) -> Optional[Any]:
    """Make a POST request to the Sentinel gateway."""
    try:
        response = httpx.post(f"{API_BASE}{path}", json=json_body or {}, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


st.set_page_config(
    page_title="Sentinel Dashboard",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Sentinel Dashboard")
st.markdown("*Cost control, execution monitoring, and failure replay for AI agents.*")

# Sidebar - Task selector
st.sidebar.header("Task Selector")

if st.sidebar.button("🔄 Refresh Tasks"):
    st.session_state.pop("tasks", None)

if "tasks" not in st.session_state:
    st.session_state.tasks = api_get("/v1/tasks") or []

tasks = st.session_state.tasks

if not tasks:
    st.sidebar.warning("No tasks found. Execute some tool calls first!")
    st.stop()

task_options = {
    f"{t['task_id'][:12]}... (${t['total_cost']:.4f}, {t['step_count']} steps)": t["task_id"]
    for t in tasks
}
selected_label = st.sidebar.selectbox("Select a task:", options=list(task_options.keys()))
selected_task_id = task_options[selected_label]

st.sidebar.markdown(f"**Full Task ID:** `{selected_task_id}`")

# Budget Card
st.header("💰 Budget Overview")

budget_data = api_get(f"/v1/tasks/{selected_task_id}/budget")
trace_data = api_get(f"/v1/tasks/{selected_task_id}/trace")

if budget_data:
    total = budget_data.get("total_budget_usd", 0.0)
    remaining = budget_data.get("remaining_budget_usd", 0.0)
    reserved = budget_data.get("reserved_budget_usd", 0.0)
    spent = total - remaining

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Budget", f"${total:.4f}")
    col2.metric("Spent", f"${spent:.4f}")
    col3.metric("Remaining", f"${remaining:.4f}")
    col4.metric("Reserved", f"${reserved:.4f}")

    if total > 0:
        progress = min(spent / total, 1.0)
        st.progress(progress, text=f"Budget used: {progress * 100:.1f}%")

        if remaining / total < 0.1:
            st.error("⚠️ Budget critically low! Less than 10% remaining.")
        elif remaining / total < 0.25:
            st.warning("⚡ Budget running low. Less than 25% remaining.")

# Burn Chart
st.header("📈 Cumulative Cost Burn")

if trace_data:
    cumulative_costs = []
    step_labels = []
    cumulative = 0.0

    for i, step in enumerate(trace_data):
        cumulative += step.get("cost_usd", 0.0)
        cumulative_costs.append(round(cumulative, 6))
        step_labels.append(f"Step {i + 1}")

    chart_data = pd.DataFrame(
        {"Step": step_labels, "Cumulative Cost (USD)": cumulative_costs}
    ).set_index("Step")

    st.line_chart(chart_data, use_container_width=True)
else:
    st.info("No trace data to chart.")

# Trace Timeline
st.header("📋 Trace Timeline")

if trace_data:
    st.write(f"**{len(trace_data)} steps recorded**")

    for i, step in enumerate(trace_data):
        status_icon = "✅" if step.get("status") == "success" else "❌"
        if step.get("status") == "budget_exceeded":
            status_icon = "🚫"
        elif step.get("status") == "circuit_breaker":
            status_icon = "⚡"

        with st.expander(
            f"{status_icon} Step {i + 1}: {step.get('tool_name', 'unknown')} "
            f"| {step.get('latency_ms', 0):.0f}ms "
            f"| ${step.get('cost_usd', 0):.6f} "
            f"| {step.get('status', 'unknown')}"
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Input Preview:**")
                st.code(step.get("input_preview", "(empty)"), language="text")
            with col2:
                st.markdown("**Output Preview:**")
                st.code(step.get("output_preview", "(empty)"), language="text")
            st.markdown("**Details:**")
            st.json(
                {
                    "step_id": step.get("step_id"),
                    "timestamp": step.get("timestamp"),
                    "model": step.get("model"),
                    "input_tokens": step.get("input_tokens"),
                    "output_tokens": step.get("output_tokens"),
                    "latency_ms": step.get("latency_ms"),
                    "cost_usd": step.get("cost_usd"),
                    "error": step.get("error"),
                }
            )
else:
    st.info("No trace data available.")

# Replay Controller
st.header("⏪ Replay Controller")

replay_key = f"replay_{selected_task_id}"
if replay_key not in st.session_state:
    state = api_get(f"/v1/tasks/{selected_task_id}/replay/state")
    if state:
        st.session_state[replay_key] = state

col_prev, col_status, col_next, col_jump = st.columns([1, 2, 1, 1])
current_state = st.session_state.get(replay_key, {})

with col_prev:
    if st.button("⬅️ Previous"):
        result = api_post(f"/v1/tasks/{selected_task_id}/replay/prev")
        if result:
            st.session_state[replay_key] = result.get("state", {})
            st.rerun()

with col_status:
    current_step = current_state.get("current_step", 0)
    total_steps = current_state.get("total_steps", 0)
    st.markdown(
        f"**Step {current_step + 1} of {total_steps}**"
        if total_steps > 0
        else "**No replay loaded**"
    )

with col_next:
    if st.button("➡️ Next"):
        result = api_post(f"/v1/tasks/{selected_task_id}/replay/next")
        if result:
            st.session_state[replay_key] = result.get("state", {})
            st.rerun()

with col_jump:
    jump_to = st.number_input(
        "Jump to:",
        min_value=1,
        max_value=total_steps if total_steps > 0 else 1,
        value=current_step + 1 if total_steps > 0 else 1,
        step=1,
        label_visibility="collapsed",
    )
    if st.button("🔢 Jump"):
        target_index = jump_to - 1
        for _ in range(target_index + 1):
            result = api_post(f"/v1/tasks/{selected_task_id}/replay/next")
            if result and result.get("state", {}).get("at_end"):
                break
        st.session_state[replay_key] = api_get(f"/v1/tasks/{selected_task_id}/replay/state") or {}
        st.rerun()

if current_state and trace_data:
    current_idx = current_state.get("current_step", 0)
    if 0 <= current_idx < len(trace_data):
        step = trace_data[current_idx]
        st.subheader(f"Replay: Step {current_idx + 1} - {step.get('tool_name', 'unknown')}")
        col_in, col_out = st.columns(2)
        with col_in:
            st.markdown("**Full Input:**")
            st.code(step.get("input_preview", "(empty)"), language="text")
        with col_out:
            st.markdown("**Full Output:**")
            st.code(step.get("output_preview", "(empty)"), language="text")
        if step.get("error"):
            st.error(f"Error: {step['error']}")

st.markdown("---")
st.caption("Sentinel v0.2.0 - Built for AI agent cost control and observability.")
