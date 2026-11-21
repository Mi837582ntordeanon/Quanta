"""A harmless local MCP server used by the disabled example configuration."""

from mcp.server import MCPServer

mcp = MCPServer("Quanta echo example")


@mcp.tool()
def echo(message: str) -> str:
    """Return the supplied text unchanged."""
    return message


if __name__ == "__main__":
    mcp.run()
