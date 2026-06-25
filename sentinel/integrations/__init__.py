"""
Sentinel — LangChain Integration

Provides a LangChain-compatible callback handler that wraps Sentinel's
budget enforcement, security, and tracing for LangChain agents.

Usage:
    from sentinel.integrations.langchain import SentinelCallback

    callback = SentinelCallback(agent_id="my-agent", budget_usd=5.0)
    agent_executor.invoke(input={"input": "..."}, config={"callbacks": [callback]})

Installation:
    pip install sentinel-gateway[langchain]
"""

from sentinel.integrations.langchain import SentinelCallback

__all__ = ["SentinelCallback"]
