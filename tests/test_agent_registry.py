"""Tests for Sentinel agent registry and authentication."""
from __future__ import annotations

from sentinel.agent_registry import AgentRegistry, AgentIdentity


class TestAgentRegistry:
    def setup_method(self):
        import tempfile
        from pathlib import Path
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "test_agents.db"

    def teardown_method(self):
        import shutil
        import os
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir)

    def _make_registry(self):
        return AgentRegistry(db_path=self.db_path)

    def test_create_agent(self):
        registry = self._make_registry()
        identity, raw_key = registry.create_agent("test-agent", max_budget_usd=5.0)

        assert identity.agent_id == "test-agent"
        assert identity.max_budget_usd == 5.0
        assert identity.enabled is True
        assert raw_key.startswith("af_")
        assert len(raw_key) > 40

    def test_authenticate_valid_key(self):
        registry = self._make_registry()
        _, raw_key = registry.create_agent("auth-test")

        agent = registry.authenticate(raw_key)
        assert agent is not None
        assert agent.agent_id == "auth-test"

    def test_authenticate_invalid_key(self):
        registry = self._make_registry()
        agent = registry.authenticate("invalid_key_12345")
        assert agent is None

    def test_authenticate_empty_key(self):
        registry = self._make_registry()
        agent = registry.authenticate("")
        assert agent is None

    def test_authenticate_disabled_agent(self):
        registry = self._make_registry()
        _, raw_key = registry.create_agent("disabled-test")
        registry.disable_agent("disabled-test")

        agent = registry.authenticate(raw_key)
        assert agent is None

    def test_disable_and_enable(self):
        registry = self._make_registry()
        _, raw_key = registry.create_agent("toggle-test")

        # Disable
        assert registry.disable_agent("toggle-test") is True
        assert registry.authenticate(raw_key) is None

        # Enable
        assert registry.enable_agent("toggle-test") is True
        agent = registry.authenticate(raw_key)
        assert agent is not None
        assert agent.agent_id == "toggle-test"

    def test_get_agent(self):
        registry = self._make_registry()
        registry.create_agent("lookup-test", agent_name="Lookup Test")

        agent = registry.get_agent("lookup-test")
        assert agent is not None
        assert agent.agent_name == "Lookup Test"

    def test_get_nonexistent_agent(self):
        registry = self._make_registry()
        assert registry.get_agent("nonexistent") is None

    def test_list_agents(self):
        registry = self._make_registry()
        registry.create_agent("agent-1")
        registry.create_agent("agent-2")
        registry.create_agent("agent-3")

        agents = registry.list_agents()
        assert len(agents) == 3
        agent_ids = {a.agent_id for a in agents}
        assert "agent-1" in agent_ids
        assert "agent-2" in agent_ids
        assert "agent-3" in agent_ids

    def test_delete_agent(self):
        registry = self._make_registry()
        registry.create_agent("delete-me")

        assert registry.delete_agent("delete-me") is True
        assert registry.get_agent("delete-me") is None

    def test_delete_nonexistent(self):
        registry = self._make_registry()
        assert registry.delete_agent("nonexistent") is False

    def test_unique_api_keys(self):
        registry = self._make_registry()
        _, key1 = registry.create_agent("key-test-1")
        _, key2 = registry.create_agent("key-test-2")

        assert key1 != key2

    def test_agent_identity_to_dict(self):
        identity = AgentIdentity(
            agent_id="dict-test",
            api_key_hash="abc123",
            agent_name="Dict Test",
            max_budget_usd=25.0,
        )
        d = identity.to_dict()
        assert d["agent_id"] == "dict-test"
        assert d["max_budget_usd"] == 25.0
        assert d["enabled"] is True
