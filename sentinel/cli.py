"""
Sentinel CLI — Command-line interface for managing Sentinel.

Usage:
    sentinel start          # Start the gateway
    sentinel dashboard      # Start the dashboard
    sentinel status         # Check gateway health
    sentinel agents list    # List registered agents
    sentinel agents add     # Register a new agent
    sentinel audit          # View security audit log
    sentinel audit verify   # Verify audit chain integrity
    sentinel sandbox list   # List tool sandbox policies
    sentinel version        # Show version info
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

__version__ = "0.3.0"


def _get_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Sentinel — Security-aware infrastructure for AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  sentinel start              # Start gateway\n  sentinel dashboard          # Start dashboard\n  sentinel audit --verify     # Verify audit chain\n  sentinel sandbox --list     # List tool policies\n",
    )

    parser.add_argument("--version", action="version", version=f"Sentinel v{__version__}")
    parser.add_argument("--config", type=str, default=None, help="Path to .env config file")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- start ---
    start_parser = subparsers.add_parser("start", help="Start the Sentinel gateway")
    start_parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind host")
    start_parser.add_argument("--port", type=int, default=8000, help="Bind port")
    start_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    start_parser.add_argument("--workers", type=int, default=1, help="Number of workers")

    # --- dashboard ---
    dash_parser = subparsers.add_parser("dashboard", help="Start the Streamlit dashboard")
    dash_parser.add_argument("--port", type=int, default=8501, help="Dashboard port")

    # --- status ---
    subparsers.add_parser("status", help="Check gateway health status")

    # --- agents ---
    agents_parser = subparsers.add_parser("agents", help="Manage agent identities")
    agents_sub = agents_parser.add_subparsers(dest="agents_command")

    agents_list = agents_sub.add_parser("list", help="List registered agents")
    agents_list.add_argument("--json", action="store_true", help="Output as JSON")

    agents_add = agents_sub.add_parser("add", help="Register a new agent")
    agents_add.add_argument("agent_id", type=str, help="Unique agent ID")
    agents_add.add_argument("--name", type=str, default="", help="Human-readable name")
    agents_add.add_argument("--budget", type=float, default=10.0, help="Max budget in USD")
    agents_add.add_argument("--rpm", type=int, default=30, help="Max requests per minute")

    agents_rm = agents_sub.add_parser("remove", help="Remove an agent")
    agents_rm.add_argument("agent_id", type=str, help="Agent ID to remove")

    # --- audit ---
    audit_parser = subparsers.add_parser("audit", help="View security audit log")
    audit_parser.add_argument("--agent", type=str, default=None, help="Filter by agent ID")
    audit_parser.add_argument("--risk", type=str, default=None, choices=["low", "medium", "high", "critical"])
    audit_parser.add_argument("--limit", type=int, default=50, help="Max events to show")
    audit_parser.add_argument("--verify", action="store_true", help="Verify chain integrity")
    audit_parser.add_argument("--summary", action="store_true", help="Show summary")
    audit_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # --- sandbox ---
    sandbox_parser = subparsers.add_parser("sandbox", help="Manage tool sandbox policies")
    sandbox_parser.add_argument("--list", action="store_true", help="List all policies")
    sandbox_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # --- init ---
    init_parser = subparsers.add_parser("init", help="Initialize a new Sentinel project")
    init_parser.add_argument("--path", type=str, default=".", help="Project directory")

    # --- deploy ---
    deploy_parser = subparsers.add_parser("deploy", help="Deployment helpers")
    deploy_sub = deploy_parser.add_subparsers(dest="deploy_command")
    deploy_docker = deploy_sub.add_parser("docker", help="Deploy with Docker Compose")
    deploy_docker.add_argument("--detach", "-d", action="store_true", help="Run in background")

    deploy_tunnel = deploy_sub.add_parser("tunnel", help="Expose via Cloudflare Tunnel")
    deploy_tunnel.add_argument("--port", type=int, default=8000, help="Local port to tunnel")

    return parser


def cmd_start(args: argparse.Namespace) -> None:
    """Start the gateway server."""
    import uvicorn

    print(f"🛡️  Sentinel v{__version__} — Starting gateway...")
    print(f"   Host: {args.host}:{args.port}")
    print(f"   Workers: {args.workers}")
    print(f"   Docs: http://{args.host}:{args.port}/docs")
    print()

    uvicorn.run(
        "sentinel.gateway:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        log_level="info",
    )


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Start the Streamlit dashboard."""
    import subprocess

    print(f"🛡️  Sentinel v{__version__} — Starting dashboard on port {args.port}...")
    subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run",
            "dashboard/app.py",
            "--server.port", str(args.port),
            "--server.address", "0.0.0.0",
            "--server.headless", "true",
        ],
        check=True,
    )


def cmd_status(args: argparse.Namespace) -> None:
    """Check gateway health."""
    import urllib.request

    try:
        req = urllib.request.urlopen("http://localhost:8000/health", timeout=5)
        data = json.loads(req.read().decode())
        print("🛡️  Sentinel Status")
        print(f"   Status:    {data.get('status', 'unknown')}")
        print(f"   Version:   {data.get('version', 'unknown')}")
        print(f"   Uptime:    {data.get('uptime_seconds', 0):.1f}s")
        print(f"   Mock Mode: {data.get('mock_mode', 'unknown')}")
        budget = data.get("budget_enforcer", {})
        tracer = data.get("tracer", {})
        print(f"   Budget DB: {budget.get('status', 'unknown')} ({budget.get('task_count', 0)} tasks)")
        print(f"   Tracer DB: {tracer.get('status', 'unknown')} ({tracer.get('step_count', 0)} steps)")
    except Exception as e:
        print(f"❌ Gateway not reachable: {e}")
        sys.exit(1)


def cmd_agents_list(args: argparse.Namespace) -> None:
    """List registered agents."""
    from sentinel.agent_registry import AgentRegistry

    registry = AgentRegistry()
    agents = registry.list_agents()

    if not agents:
        print("No agents registered.")
        return

    if args.json:
        print(json.dumps([a.to_dict() for a in agents], indent=2))
    else:
        print(f"{'Agent ID':<30} {'Name':<20} {'Budget':>10} {'RPM':>5} {'Enabled':>8}")
        print("-" * 80)
        for a in agents:
            print(
                f"{a.agent_id:<30} {a.agent_name:<20} "
                f"${a.max_budget_usd:>8.2f} {a.max_requests_per_minute:>5} "
                f"{'✓' if a.enabled else '✗':>8}"
            )


def cmd_agents_add(args: argparse.Namespace) -> None:
    """Register a new agent."""
    from sentinel.agent_registry import AgentRegistry

    registry = AgentRegistry()
    identity, raw_key = registry.create_agent(
        agent_id=args.agent_id,
        agent_name=args.name,
        max_budget_usd=args.budget,
        max_requests_per_minute=args.rpm,
    )
    print(f"✅ Agent '{args.agent_id}' registered successfully")
    print(f"   Budget: ${args.budget:.2f}")
    print(f"   RPM:    {args.rpm}")
    print("")
    print("   🔑 API Key (save this — it won't be shown again):")
    print(f"   {raw_key}")


def cmd_agents_remove(args: argparse.Namespace) -> None:
    """Remove an agent."""
    from sentinel.agent_registry import AgentRegistry

    registry = AgentRegistry()
    if registry.delete_agent(args.agent_id):
        print(f"✅ Agent '{args.agent_id}' removed")
    else:
        print(f"⚠️  Agent '{args.agent_id}' not found")


def cmd_audit(args: argparse.Namespace) -> None:
    """View security audit log."""
    from sentinel.security import AuditLogger, RiskLevel

    audit = AuditLogger()

    if args.verify:
        print("🔍 Verifying audit chain integrity...")
        is_valid, errors = audit.verify_chain()
        if is_valid:
            print("✅ Audit chain is intact — no tampering detected")
        else:
            print("❌ AUDIT CHAIN COMPROMISED!")
            for err in errors:
                print(f"   ⚠️  {err}")
            sys.exit(1)
        return

    if args.summary:
        summary = audit.get_summary()
        print("📊 Audit Summary")
        print(f"   Total Events:    {summary.get('total_events', 0)}")
        print(f"   Unique Agents:   {summary.get('unique_agents', 0)}")
        print(f"   Critical Events: {summary.get('critical_events', 0)}")
        print(f"   High Events:     {summary.get('high_events', 0)}")
        print(f"   Blocked Events:  {summary.get('blocked_events', 0)}")
        return

    risk = RiskLevel(args.risk) if args.risk else None
    events = audit.get_events(
        agent_id=args.agent,
        risk_level=risk,
        limit=args.limit,
    )

    if not events:
        print("No audit events found.")
        return

    if args.json:
        print(json.dumps(events, indent=2, default=str))
    else:
        print(f"{'Time':<22} {'Event Type':<30} {'Agent':<20} {'Risk':<10} {'Details'}")
        print("-" * 110)
        for ev in events:
            details = ev.get("details", "{}")
            if len(details) > 40:
                details = details[:40] + "..."
            print(
                f"{ev['timestamp']:<22} {ev['event_type']:<30} "
                f"{ev.get('agent_id', ''):<20} {ev.get('risk_level', ''):<10} {details}"
            )


def cmd_sandbox(args: argparse.Namespace) -> None:
    """List tool sandbox policies."""
    from sentinel.security import ToolSandbox

    sandbox = ToolSandbox()
    policies = sandbox.list_policies()

    if args.json:
        output = {}
        for name, policy in policies.items():
            output[name] = {
                "allowed": policy.allowed,
                "max_input_length": policy.max_input_length,
                "max_output_tokens": policy.max_output_tokens,
                "require_budget": policy.require_budget,
                "risk_level": policy.risk_level.value,
            }
        print(json.dumps(output, indent=2))
    else:
        print(f"{'Tool':<25} {'Allowed':<10} {'Risk':<10} {'Max Input':<12} {'Budget':<8}")
        print("-" * 75)
        for name in sorted(policies.keys()):
            p = policies[name]
            print(
                f"{name:<25} {'✓' if p.allowed else '✗':<10} "
                f"{p.risk_level.value:<10} {p.max_input_length:<12} "
                f"{'✓' if p.require_budget else '✗':<8}"
            )


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new Sentinel project."""
    project_path = Path(args.path)
    project_path.mkdir(parents=True, exist_ok=True)

    # Create .env.example
    env_example = project_path / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            "# Sentinel Configuration\n"
            "AF_API_KEY=your-api-key-here\n"
            "AF_MOCK_MODE=true\n"
            "AF_LOG_LEVEL=INFO\n"
            "AF_LOG_FORMAT=readable\n",
            encoding="utf-8",
        )

    # Create sentinel.yaml config
    config_file = project_path / "sentinel.yaml"
    if not config_file.exists():
        config_file.write_text(
            "# Sentinel Project Configuration\n"
            "gateway:\n"
            "  host: 0.0.0.0\n"
            "  port: 8000\n"
            "security:\n"
            "  default_policy: deny\n"
            "  rate_limit_rpm: 60\n"
            "budget:\n"
            "  default_usd: 1.0\n",
            encoding="utf-8",
        )

    print(f"✅ Sentinel project initialized at {project_path}")
    print(f"   Edit {config_file.name} to configure")
    print("   Run 'sentinel start' to begin")


def cmd_deploy_docker(args: argparse.Namespace) -> None:
    """Deploy with Docker Compose."""
    import subprocess

    cmd = ["docker", "compose", "up"]
    if args.detach:
        cmd.append("-d")

    print("🛡️  Deploying Sentinel with Docker Compose...")
    subprocess.run(cmd, check=True)

    if args.detach:
        print("✅ Sentinel running in background")
        print("   Gateway:   http://localhost:8000")
        print("   Dashboard: http://localhost:8501")
        print("   Logs:      docker compose logs -f")


def cmd_deploy_tunnel(args: argparse.Namespace) -> None:
    """Expose via Cloudflare Tunnel."""
    import subprocess

    print(f"🛡️  Creating Cloudflare Tunnel for port {args.port}...")
    print("   Make sure 'cloudflared' is installed: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/")

    try:
        subprocess.run(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{args.port}"],
            check=True,
        )
    except FileNotFoundError:
        print("❌ 'cloudflared' not found. Install it from:")
        print("   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)


def main(argv: Optional[list[str]] = None) -> None:
    """Main CLI entry point."""
    parser = _get_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    dispatch = {
        "start": cmd_start,
        "dashboard": cmd_dashboard,
        "status": cmd_status,
        "init": cmd_init,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    elif args.command == "agents":
        if args.agents_command == "list":
            cmd_agents_list(args)
        elif args.agents_command == "add":
            cmd_agents_add(args)
        elif args.agents_command == "remove":
            cmd_agents_remove(args)
        else:
            parser.parse_args(["agents", "--help"])
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "sandbox":
        cmd_sandbox(args)
    elif args.command == "deploy":
        if args.deploy_command == "docker":
            cmd_deploy_docker(args)
        elif args.deploy_command == "tunnel":
            cmd_deploy_tunnel(args)
        else:
            parser.parse_args(["deploy", "--help"])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
