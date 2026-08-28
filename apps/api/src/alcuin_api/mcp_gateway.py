from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .contracts import MCPEntrypoint


class MCPGateway:
    """Backend-only MCP transport gateway; browser clients never spawn MCP processes."""

    @asynccontextmanager
    async def session(self, entrypoint: MCPEntrypoint) -> AsyncIterator[ClientSession]:
        if entrypoint.transport == "stdio":
            parameters = StdioServerParameters(
                command=entrypoint.command or "",
                args=entrypoint.args,
                cwd=entrypoint.cwd,
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=10),
                ) as session:
                    await session.initialize()
                    yield session
            return
        if entrypoint.transport == "sse":
            async with sse_client(str(entrypoint.url)) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=10),
                ) as session:
                    await session.initialize()
                    yield session
            return
        async with streamable_http_client(str(entrypoint.url)) as (
            read_stream,
            write_stream,
            _session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=10),
            ) as session:
                await session.initialize()
                yield session

    async def discover(self, entrypoint: MCPEntrypoint) -> list[dict[str, Any]]:
        async with self.session(entrypoint) as session:
            result = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                    "output_schema": tool.outputSchema,
                    "annotations": tool.annotations.model_dump(mode="json") if tool.annotations else None,
                }
                for tool in result.tools
            ]

    async def call(
        self,
        entrypoint: MCPEntrypoint,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.session(entrypoint) as session:
            result = await session.call_tool(tool_name, arguments)
            return {
                "is_error": bool(result.isError),
                "content": [item.model_dump(mode="json") for item in result.content],
                "structured_content": result.structuredContent,
            }
