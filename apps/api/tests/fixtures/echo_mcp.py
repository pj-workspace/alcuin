from mcp.server.fastmcp import FastMCP

mcp = FastMCP("alcuin-test-echo", json_response=True)


@mcp.tool()
def echo(message: str) -> dict[str, str]:
    """Return a message for transport verification."""
    return {"message": message}


if __name__ == "__main__":
    mcp.run(transport="stdio")
