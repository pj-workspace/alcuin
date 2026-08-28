from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("alcuin-test-echo", json_response=True)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def echo(message: str) -> dict[str, str]:
    """Return a message for transport verification."""
    return {"message": message}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def write_marker(value: str) -> dict[str, str]:
    """Represent a mutating operation for permission-selection tests."""
    return {"value": value}


if __name__ == "__main__":
    mcp.run(transport="stdio")
