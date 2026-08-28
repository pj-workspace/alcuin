from __future__ import annotations

import sys
from pathlib import Path

import pytest

from alcuin_api.contracts import MCPEntrypoint
from alcuin_api.mcp_gateway import MCPGateway


@pytest.mark.asyncio
async def test_stdio_mcp_discovery_and_call() -> None:
    server = Path(__file__).parent / "fixtures" / "echo_mcp.py"
    entrypoint = MCPEntrypoint(
        transport="stdio",
        command=sys.executable,
        args=[str(server)],
    )
    gateway = MCPGateway()
    tools = await gateway.discover(entrypoint)
    assert [tool["name"] for tool in tools] == ["echo", "write_marker"]
    result = await gateway.call(entrypoint, "echo", {"message": "hello from Alcuin"})
    assert result["is_error"] is False
    assert result["structured_content"] == {"message": "hello from Alcuin"}
