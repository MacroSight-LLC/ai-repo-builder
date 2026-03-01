#!/usr/bin/env python3
"""Smoke test: verify MCP servers can start and respond via stdio."""

import asyncio
import json
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_single_server(name: str, command: str, args: list[str], timeout_s: int = 30):
    """Start one MCP server via stdio and send initialize."""
    print(f"  ...  {name}: starting", flush=True)
    env = {**os.environ}
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        print(f"  SKIP {name}: command '{command}' not found")
        return False

    init_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "1.0"},
            },
        }
    )
    content = f"Content-Length: {len(init_msg)}\r\n\r\n{init_msg}"

    try:
        proc.stdin.write(content.encode())
        await proc.stdin.drain()

        header = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout_s)
        if not header:
            stderr_out = await proc.stderr.read(500)
            print(f"  FAIL {name}: no response. stderr={stderr_out.decode()[:200]}")
            proc.terminate()
            return False

        content_length = int(header.decode().split(":")[1].strip())
        await proc.stdout.readline()  # empty line separator
        body = await asyncio.wait_for(proc.stdout.read(content_length), timeout=5)
        resp = json.loads(body)

        server_info = resp.get("result", {}).get("serverInfo", {})
        server_name = server_info.get("name", "?")
        server_version = server_info.get("version", "?")
        print(f"  OK   {name}: {server_name} v{server_version}")
        proc.terminate()
        return True

    except TimeoutError:
        stderr_out = await proc.stderr.read(500)
        print(f"  FAIL {name}: timeout after {timeout_s}s. stderr={stderr_out.decode()[:200]}")
        proc.terminate()
        return False
    except Exception as e:
        print(f"  FAIL {name}: {e}")
        proc.terminate()
        return False


async def main():
    import yaml
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    # GitHub MCP expects GITHUB_PERSONAL_ACCESS_TOKEN
    if os.environ.get("GITHUB_TOKEN") and not os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = os.environ["GITHUB_TOKEN"]

    config_path = os.path.join(os.path.dirname(__file__), "..", "mcp_servers_local.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    servers = config.get("mcpServers", {})
    print(f"\nTesting {len(servers)} MCP servers via stdio...\n")

    results = {}
    for name, cfg in servers.items():
        command = cfg.get("command", "npx")
        args = cfg.get("args", [])
        ok = await test_single_server(name, command, args)
        results[name] = ok

    print(f"\n{'=' * 50}")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"  {passed}/{total} servers responded successfully")
    print(f"{'=' * 50}\n")

    if passed < total:
        failed = [n for n, v in results.items() if not v]
        print(f"  Failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
