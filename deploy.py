#!/usr/bin/env python3
"""
AgentFence Deploy Script — One-command deployment.

Usage:
    python deploy.py              # Interactive mode
    python deploy.py --docker     # Deploy with Docker Compose
    python deploy.py --local      # Deploy locally (install + start)
    python deploy.py --tunnel     # Expose via Cloudflare Tunnel
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command with error handling."""
    print(f"  $ {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, check=True, **kwargs)
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Command failed with exit code {e.returncode}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"  ❌ Command not found: {cmd[0]}")
        sys.exit(1)


def check_python() -> None:
    """Check Python version."""
    version = sys.version_info
    if version < (3, 11):
        print(f"  ❌ Python 3.11+ required (found {version.major}.{version.minor})")
        sys.exit(1)
    print(f"  ✅ Python {version.major}.{version.minor}.{version.micro}")


def check_docker() -> bool:
    """Check if Docker is available."""
    return shutil.which("docker") is not None


def check_cloudflared() -> bool:
    """Check if cloudflared is available."""
    return shutil.which("cloudflared") is not None


def deploy_local(args: argparse.Namespace) -> None:
    """Deploy AgentFence locally."""
    print("\n🛡️  AgentFence Local Deployment")
    print("=" * 50)

    # Check Python
    print("\n📋 Checking prerequisites...")
    check_python()

    # Install dependencies
    print("\n📦 Installing dependencies...")
    python_cmd = sys.executable
    run([python_cmd, "-m", "pip", "install", "-e", ".[dev]"],
        cwd=str(PROJECT_ROOT))

    # Create .env if not exists
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print("\n📝 Creating .env file...")
        env_example = PROJECT_ROOT / ".env.example"
        if env_example.exists():
            shutil.copy(env_example, env_file)
        else:
            env_file.write_text(
                "AF_MOCK_MODE=true\n"
                "AF_LOG_LEVEL=INFO\n"
                "AF_LOG_FORMAT=readable\n"
            )
        print("  ✅ Created .env (edit to add your API key)")

    # Run tests
    if not args.skip_tests:
        print("\n🧪 Running tests...")
        run([python_cmd, "-m", "pytest", "tests/", "-v"],
            cwd=str(PROJECT_ROOT))

    print("\n✅ AgentFence installed successfully!")
    print("\nQuick start:")
    print("  agentfence start          # Start the gateway")
    print("  agentfence dashboard      # Start the dashboard")
    print("  agentfence status         # Check health")
    print("  agentfence audit          # View security log")
    print("  agentfence sandbox --list # List tool policies")
    print()


def deploy_docker(args: argparse.Namespace) -> None:
    """Deploy AgentFence with Docker Compose."""
    print("\n🛡️  AgentFence Docker Deployment")
    print("=" * 50)

    if not check_docker():
        print("  ❌ Docker not found. Install Docker Desktop first.")
        print("     https://www.docker.com/products/docker-desktop/")
        sys.exit(1)

    print("\n📋 Docker found ✅")

    # Create .env if not exists
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print("\n📝 Creating .env file...")
        env_file.write_text(
            "AF_MOCK_MODE=true\n"
            "AF_LOG_LEVEL=INFO\n"
            "AF_LOG_FORMAT=json\n"
            "AF_DATABASE_URL=sqlite+aiosqlite:///./data/agentfence.db\n"
            "AF_TRACES_DIR=/app/traces\n"
        )

    # Build and start
    print("\n🐳 Building and starting Docker containers...")
    run(["docker", "compose", "build"], cwd=str(PROJECT_ROOT))
    cmd = ["docker", "compose", "up"]
    if args.detach:
        cmd.append("-d")
    run(cmd, cwd=str(PROJECT_ROOT))

    if args.detach:
        print("\n✅ AgentFence running in background!")
        print("   Gateway:   http://localhost:8000")
        print("   Dashboard: http://localhost:8501")
        print("   Logs:      docker compose logs -f")
        print("   Stop:      docker compose down")
    print()


def deploy_tunnel(args: argparse.Namespace) -> None:
    """Expose AgentFence via Cloudflare Tunnel."""
    print("\n🛡️  AgentFence Cloudflare Tunnel")
    print("=" * 50)

    if not check_cloudflared():
        print("  ❌ cloudflared not found.")
        print("     Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)

    print("\n📋 cloudflared found ✅")
    port = args.port

    # Check if gateway is running
    import urllib.request
    try:
        urllib.request.urlopen(f"http://localhost:{port}/health", timeout=3)
        print(f"  ✅ Gateway running on port {port}")
    except Exception:
        print(f"  ⚠️  Gateway not detected on port {port}")
        print(f"     Start it first: agentfence start --port {port}")
        if not args.force:
            sys.exit(1)

    print(f"\n🌐 Creating Cloudflare Tunnel for port {port}...")
    print("   (This will print a public URL when ready)")
    print()

    run(["cloudflared", "tunnel", "--url", f"http://localhost:{port}"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentFence Deploy Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  python deploy.py --local         # Install and run locally\n  python deploy.py --docker -d     # Deploy with Docker in background\n  python deploy.py --tunnel        # Expose via Cloudflare Tunnel\n",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--local", action="store_true", help="Deploy locally")
    group.add_argument("--docker", action="store_true", help="Deploy with Docker Compose")
    group.add_argument("--tunnel", action="store_true", help="Expose via Cloudflare Tunnel")

    parser.add_argument("--detach", "-d", action="store_true", help="Docker: run in background")
    parser.add_argument("--port", type=int, default=8000, help="Port for tunnel (default: 8000)")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running tests")
    parser.add_argument("--force", action="store_true", help="Force tunnel even if gateway not detected")

    args = parser.parse_args()

    if args.local:
        deploy_local(args)
    elif args.docker:
        deploy_docker(args)
    elif args.tunnel:
        deploy_tunnel(args)


if __name__ == "__main__":
    main()
