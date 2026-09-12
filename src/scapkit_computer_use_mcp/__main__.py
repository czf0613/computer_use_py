"""Command-line entry point; imports optional dependencies only when serving."""

import argparse
import ipaddress
import os


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Computer Use MCP server (FastAPI Streamable HTTP or stdio)"
    )
    parser.add_argument(
        "--transport", choices=["streamable-http", "stdio"], default="streamable-http"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--token-env",
        default="SCAPKIT_MCP_TOKEN",
        help="Environment variable holding the bearer token; the token is never passed on the command line",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Additional accepted HTTP Host, e.g. device.example:8000; repeat as needed",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Allowed browser Origin (exact URL); repeat as needed",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    token = os.environ.get(args.token_env)
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = args.host == "localhost"
    if args.transport == "streamable-http" and not loopback:
        if not token:
            parser.error(
                "non-loopback binding requires a bearer token in " + args.token_env
            )
        if not args.allowed_host:
            parser.error(
                "non-loopback binding requires --allowed-host for the public hostname or IP"
            )
    try:
        import uvicorn

        from .server import LOCAL_HOSTS, create_app, create_server
    except ModuleNotFoundError as error:
        parser.error(
            f"Optional MCP dependency {error.name!r} is missing. Install 'scapkit_computer_use[mcp]'."
        )
    if args.transport == "stdio":
        create_server().run("stdio")
    else:
        hosts = [*LOCAL_HOSTS, *args.allowed_host]
        if loopback and args.host != "localhost":
            host = f"[{args.host}]" if ":" in args.host else args.host
            hosts.extend([host, f"{host}:*"])
        app = create_app(
            token=token,
            allowed_hosts=hosts,
            allowed_origins=args.allowed_origin or None,
        )
        uvicorn.run(
            app, host=args.host, port=args.port, workers=1, timeout_graceful_shutdown=10
        )


if __name__ == "__main__":
    main()
