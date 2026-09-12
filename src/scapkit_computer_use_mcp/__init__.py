"""Optional MCP integration. Install scapkit_computer_use[mcp] to run it."""

__all__ = ["create_app", "create_server"]


def create_app(**kwargs):
    """Create the FastAPI application; see server.create_app for options."""
    from .server import create_app as factory

    return factory(**kwargs)


def create_server(**kwargs):
    """Create the MCP server for embedding or stdio transport."""
    from .server import create_server as factory

    return factory(**kwargs)
