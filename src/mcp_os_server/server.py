#!/usr/bin/env python3
"""
MCP OS Server

which includes a command server implementation, filesystem server implementation,
and web management interface.
"""

import asyncio
import logging
import os
import platform
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import List, Optional
import socket

import click
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from mcp.types import Tool as MCPTool
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate

from .command.command_executor import CommandExecutor
from .command.output_manager import OutputManager
from .command.process_manager import ProcessManager
from .command.web_manager import WebManager
from .filesystem.server import create_server as create_filesystem_server
from .filtered_fast_mcp import FilteredFastMCP





def setup_logger(mode: str) -> logging.Logger:
    """Setup logger based on server mode."""
    logger = logging.getLogger("mcp_os_server")
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # In stdio mode, minimize logging to avoid interfering with MCP protocol
    if mode == "stdio":
        # Still output to stderr to avoid stdout interference
        stream = sys.stderr
    else:
        # In other modes, use normal INFO level
        logger.setLevel(logging.INFO)
        stream = sys.stdout
    
    handler = logging.StreamHandler(stream)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


def parse_allowed_commands(allowed_commands_str: str) -> List[str]:
    """Parse the ALLOWED_COMMANDS environment variable."""
    if not allowed_commands_str:
        return []
    
    # Split by comma and strip whitespace
    commands = [cmd.strip() for cmd in allowed_commands_str.split(",")]
    # Filter out empty strings
    return [cmd for cmd in commands if cmd]


def parse_allowed_directories(allowed_dirs_str: str) -> List[str]:
    """Parse the ALLOWED_DIRS environment variable."""
    if not allowed_dirs_str:
        return []
    
    # Split by comma and strip whitespace
    dirs = [dir_path.strip() for dir_path in allowed_dirs_str.split(",")]
    # Filter out empty strings
    return [dir_path for dir_path in dirs if dir_path]





def get_default_encoding() -> str:
    """Get the default encoding for the current platform."""
    if platform.system() == "Windows":
        return "gbk"
    else:
        return "utf-8"


async def create_command_executor(
    output_storage_path: str,
    process_retention_seconds: int,
    default_encoding: str,
) -> CommandExecutor:
    """Create and initialize a CommandExecutor instance."""
    # Create OutputManager
    output_manager = OutputManager(output_storage_path=Path(output_storage_path).absolute().as_posix())
    
    # Create ProcessManager
    process_manager = ProcessManager(
        output_manager=output_manager,
        process_retention_seconds=process_retention_seconds
    )
    
    # Create CommandExecutor
    command_executor = CommandExecutor(
        process_manager=process_manager,
        default_encoding=default_encoding,
        limit_lines=500,
    )
    
    # Initialize the executor
    await command_executor.initialize()
    
    return command_executor


async def create_web_manager(command_executor: CommandExecutor) -> WebManager:
    """Create and initialize a WebManager instance."""
    web_manager = WebManager()
    await web_manager.initialize(command_executor)
    return web_manager


async def _run_filesystem_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
):
    """Run the filesystem server in the specified mode."""
    # Setup logger based on mode
    logger = setup_logger(mode)
    
    # Parse environment variables
    allowed_dirs_str = os.getenv("ALLOWED_DIRS", "")
    allowed_dirs = parse_allowed_directories(allowed_dirs_str)
    
    if not allowed_dirs:
        logger.error("Warning: No directories are allowed. Set ALLOWED_DIRS environment variable.")
        logger.error("Example: ALLOWED_DIRS='/tmp,/home/user' mcp-os-server filesystem-server")
        return
    
    # Only output initialization info in non-stdio modes
    logger.info(f"Starting MCP Filesystem Server in {mode} mode...")
    logger.info(f"Allowed directories: {', '.join(allowed_dirs)}")
    
    # Create filesystem server
    try:
        mcp = create_filesystem_server(allowed_dirs, host=host, port=port)
    except Exception as e:
        logger.error(f"Failed to initialize filesystem server: {e}")
        return
    
    try:
        if mode == "stdio":
            # Run in stdio mode (default) - use async version
            await mcp.run_stdio_async()
        elif mode == "sse":
            # Run in SSE mode
            logger.info(f"Starting SSE server on {host}:{port}")
            logger.info(f"MCP web interface available at: http://{host}:{port}{web_path}")
            await mcp.run_sse_async()
        elif mode == "http":
            # Run in HTTP mode
            logger.info(f"Starting HTTP server on {host}:{port}")
            logger.info(f"MCP API endpoint: http://{host}:{port}{path}")
            logger.info(f"MCP web interface available at: http://{host}:{port}{web_path}")
            await mcp.run_streamable_http_async()
    except KeyboardInterrupt:
        logger.info("Shutting down filesystem server...")
    except Exception as e:
        import traceback
        logger.error(f"Filesystem server error: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")


async def _run_unified_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    web_host: str,
    web_port: int,
    enable_web_manager: bool,
    web_debug: bool,
):
    """Run both command and filesystem servers in the specified mode."""
    # Setup logger based on mode
    logger = setup_logger(mode)
    
    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None
    
    # Parse environment variables for command server
    allowed_commands_str = os.getenv("ALLOWED_COMMANDS", "")
    allowed_commands = parse_allowed_commands(allowed_commands_str)
    
    # Parse environment variables for filesystem server
    allowed_dirs_str = os.getenv("ALLOWED_DIRS", "")
    allowed_dirs = parse_allowed_directories(allowed_dirs_str)
    
    if not allowed_commands and not allowed_dirs:
        logger.error("Warning: Neither commands nor directories are allowed.")
        logger.error("Set ALLOWED_COMMANDS and/or ALLOWED_DIRS environment variables.")
        logger.error("Example: ALLOWED_COMMANDS='ls,cat,echo' ALLOWED_DIRS='/tmp,/home/user' mcp-os-server unified-server")
        return
    
    process_retention_seconds = int(os.getenv("PROCESS_RETENTION_SECONDS", "300"))
    default_encoding = os.getenv("DEFAULT_ENCODING", get_default_encoding())
    
    # OUTPUT_STORAGE_PATH logic: Use temp dir if not explicitly set
    output_storage_path = os.getenv("OUTPUT_STORAGE_PATH")
    if output_storage_path is None:
        temp_dir_obj = tempfile.TemporaryDirectory()
        output_storage_path = temp_dir_obj.name
        logger.info(f"Using temporary output storage path: {output_storage_path}")
    else:
        logger.info(f"Using provided output storage path: {output_storage_path}")

    # Only output initialization info in non-stdio modes
    logger.info(f"Starting MCP Unified Server in {mode} mode...")
    if allowed_commands:
        logger.info(f"Allowed commands: {', '.join(allowed_commands)}")
        logger.info(f"Process retention: {process_retention_seconds} seconds")
        logger.info(f"Default encoding: {default_encoding}")
    if allowed_dirs:
        logger.info(f"Allowed directories: {', '.join(allowed_dirs)}")
    
    # Create CommandExecutor if commands are allowed
    command_executor = None
    if allowed_commands:
        try:
            command_executor = await create_command_executor(
                output_storage_path=output_storage_path,
                process_retention_seconds=process_retention_seconds,
                default_encoding=default_encoding,
            )
        except Exception as e:
            logger.error(f"Failed to initialize command executor: {e}")
            return
    
    # Create and start WebManager if enabled
    web_manager = None
    if enable_web_manager and command_executor:
        try:
            web_manager = await create_web_manager(command_executor)
            await web_manager.start_web_interface(
                host=web_host,
                port=web_port,
                debug=web_debug
            )
            logger.info(f"Web management interface available at: http://{web_host}:{web_port}")
        except Exception as e:
            logger.error(f"Failed to start web manager: {e}")
            # Continue without web manager
            enable_web_manager = False
    
    # Create unified FastMCP instance
    mcp = FilteredFastMCP("mcp-unified-server", host=host, port=port)
    
    # Define command server tools if allowed
    if command_executor and allowed_commands:
        from .command.server import define_mcp_server
        define_mcp_server(
            mcp=mcp,
            command_executor=command_executor,
            allowed_commands=allowed_commands,
            default_encoding=default_encoding,
        )
    
    # Define filesystem server tools if allowed
    if allowed_dirs:
        from .filesystem.server import define_mcp_server as define_filesystem_server
        from .filesystem.filesystem_service import FilesystemService
        
        filesystem_service = FilesystemService(allowed_dirs)
        define_filesystem_server(mcp, filesystem_service)
    
    # Add command_open_web_manager tool if web manager is enabled
    if enable_web_manager:
        @mcp.tool()
        async def command_open_web_manager() -> List[TextContent]:
            """
            在浏览器中打开 Web 管理界面。
            """
            web_url = f"http://{web_host}:{web_port}"
            try:
                webbrowser.open(web_url)
                return [TextContent(type="text", text=f"已在浏览器中打开 Web 管理界面: {web_url} 🚀")]
            except Exception as e:
                return [TextContent(type="text", text=f"打开 Web 管理界面失败: {e} ❌")]
        logger.info(f"MCP tool 'command_open_web_manager' available to open: http://{web_host}:{web_port}")

    try:
        if mode == "stdio":
            # Run in stdio mode (default) - use async version
            # Minimize logging in stdio mode to avoid protocol interference
            if enable_web_manager:
                # Only log web manager info to stderr as a critical notice
                logger.error(f"Web management interface running at: http://{web_host}:{web_port}")
            await mcp.run_stdio_async()
        elif mode == "sse":
            # Run in SSE mode
            logger.info(f"Starting SSE server on {host}:{port}")
            logger.info(f"MCP web interface available at: http://{host}:{port}{web_path}")
            if enable_web_manager:
                logger.info(f"Process management interface available at: http://{web_host}:{web_port}")
            await mcp.run_sse_async()
        elif mode == "http":
            # Run in HTTP mode
            logger.info(f"Starting HTTP server on {host}:{port}")
            logger.info(f"MCP API endpoint: http://{host}:{port}{path}")
            logger.info(f"MCP web interface available at: http://{host}:{port}{web_path}")
            if enable_web_manager:
                logger.info(f"Process management interface available at: http://{web_host}:{web_port}")
            await mcp.run_streamable_http_async()
    except KeyboardInterrupt:
        # Only log shutdown message in non-stdio modes
        logger.info("Shutting down unified server...")
    except Exception as e:
        import traceback
        logger.error(f"Unified server error: {e}")
        # Only log full traceback in non-stdio modes
        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        # Cleanup
        try:
            if web_manager:
                await web_manager.shutdown()
            if command_executor:
                await command_executor.shutdown()
            if temp_dir_obj:
                temp_dir_obj.cleanup()
                logger.info(f"Cleaned up temporary output storage path: {output_storage_path}")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


def get_random_available_port() -> int:
    """Gets a random available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


@click.group()
@click.version_option()
def main():
    """MCP OS Server - A Model Context Protocol server for OS operations."""
    pass


@main.command("command-server")
@click.option(
    "--mode",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="Server mode: stdio (default), sse, or http"
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host address for SSE/HTTP modes (default: 127.0.0.1)"
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Port for SSE/HTTP modes (default: 8000)"
)
@click.option(
    "--path",
    default="/mcp",
    help="API endpoint path for HTTP mode (default: /mcp)"
)
@click.option(
    "--web-path",
    default="/web",
    help="Web interface path for SSE/HTTP modes (default: /web)"
)
@click.option(
    "--web-host",
    default="127.0.0.1",
    help="Host address for web management interface (default: 127.0.0.1)"
)
@click.option(
    "--web-port",
    type=int,
    default=get_random_available_port(),
    help="Port for web management interface (default: random available port)"
)
@click.option(
    "--enable-web-manager",
    is_flag=True,
    default=False,
    help="Enable web management interface"
)
@click.option(
    "--web-debug",
    is_flag=True,
    default=False,
    help="Use Flask development server for web interface (shows debug info)"
)
def command_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    web_host: str,
    web_port: int,
    enable_web_manager: bool,
    web_debug: bool,
):
    """Start the MCP Command Server with optional web management interface."""
    asyncio.run(_run_command_server(
        mode, host, port, path, web_path, 
        web_host, web_port, enable_web_manager, web_debug
    ))


async def _run_command_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    web_host: str,
    web_port: int,
    enable_web_manager: bool,
    web_debug: bool,
):
    """Run the command server in the specified mode."""
    # Setup logger based on mode
    logger = setup_logger(mode)
    
    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None
    
    # Parse environment variables
    allowed_commands_str = os.getenv("ALLOWED_COMMANDS", "")
    allowed_commands = parse_allowed_commands(allowed_commands_str)
    
    if not allowed_commands:
        logger.error("Warning: No commands are allowed. Set ALLOWED_COMMANDS environment variable.")
        logger.error("Example: ALLOWED_COMMANDS='ls,cat,echo' mcp-os-server command-server")
        return
    
    process_retention_seconds = int(os.getenv("PROCESS_RETENTION_SECONDS", "300"))
    default_encoding = os.getenv("DEFAULT_ENCODING", get_default_encoding())
    
    # OUTPUT_STORAGE_PATH logic: Use temp dir if not explicitly set
    output_storage_path = os.getenv("OUTPUT_STORAGE_PATH")
    if output_storage_path is None:
        temp_dir_obj = tempfile.TemporaryDirectory()
        output_storage_path = temp_dir_obj.name
        logger.info(f"Using temporary output storage path: {output_storage_path}")
    else:
        logger.info(f"Using provided output storage path: {output_storage_path}")

    # Only output initialization info in non-stdio modes
    logger.info(f"Starting MCP Command Server in {mode} mode...")
    logger.info(f"Allowed commands: {', '.join(allowed_commands)}")
    logger.info(f"Process retention: {process_retention_seconds} seconds")
    logger.info(f"Default encoding: {default_encoding}")
    
    # Create CommandExecutor
    try:
        command_executor = await create_command_executor(
            output_storage_path=output_storage_path,
            process_retention_seconds=process_retention_seconds,
            default_encoding=default_encoding,
        )
    except Exception as e:
        logger.error(f"Failed to initialize command executor: {e}")
        return
    
    # Create and start WebManager if enabled
    web_manager = None
    if enable_web_manager:
        try:
            web_manager = await create_web_manager(command_executor)
            await web_manager.start_web_interface(
                host=web_host,
                port=web_port,
                debug=web_debug
            )
            logger.info(f"Web management interface available at: http://{web_host}:{web_port}")
        except Exception as e:
            logger.error(f"Failed to start web manager: {e}")
            # Continue without web manager
            enable_web_manager = False
    
    # Create FastMCP instance
    mcp = FilteredFastMCP("mcp-command-server", host=host, port=port)
    
    # Define MCP tools
    from .command.server import define_mcp_server
    define_mcp_server(
        mcp=mcp,
        command_executor=command_executor,
        allowed_commands=allowed_commands,
        default_encoding=default_encoding,
    )
    
    # Add command_open_web_manager tool if web manager is enabled
    if enable_web_manager:
        @mcp.tool()
        async def command_open_web_manager() -> List[TextContent]:
            """
            在浏览器中打开 Web 管理界面。
            """
            web_url = f"http://{web_host}:{web_port}"
            try:
                webbrowser.open(web_url)
                return [TextContent(type="text", text=f"已在浏览器中打开 Web 管理界面: {web_url} 🚀")]
            except Exception as e:
                return [TextContent(type="text", text=f"打开 Web 管理界面失败: {e} ❌")]
        logger.info(f"MCP tool 'command_open_web_manager' available to open: http://{web_host}:{web_port}")

    try:
        if mode == "stdio":
            # Run in stdio mode (default) - use async version
            # Minimize logging in stdio mode to avoid protocol interference
            if enable_web_manager:
                # Only log web manager info to stderr as a critical notice
                logger.error(f"Web management interface running at: http://{web_host}:{web_port}")
            await mcp.run_stdio_async()
        elif mode == "sse":
            # Run in SSE mode
            logger.info(f"Starting SSE server on {host}:{port}")
            logger.info(f"MCP web interface available at: http://{host}:{port}{web_path}")
            if enable_web_manager:
                logger.info(f"Process management interface available at: http://{web_host}:{web_port}")
            await mcp.run_sse_async()
        elif mode == "http":
            # Run in HTTP mode
            logger.info(f"Starting HTTP server on {host}:{port}")
            logger.info(f"MCP API endpoint: http://{host}:{port}{path}")
            logger.info(f"MCP web interface available at: http://{host}:{port}{web_path}")
            if enable_web_manager:
                logger.info(f"Process management interface available at: http://{web_host}:{web_port}")
            await mcp.run_streamable_http_async()
    except KeyboardInterrupt:
        # Only log shutdown message in non-stdio modes
        logger.info("Shutting down server...")
    except Exception as e:
        import traceback
        logger.error(f"Server error: {e}")
        # Only log full traceback in non-stdio modes
        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        # Cleanup
        try:
            if web_manager:
                await web_manager.shutdown()
            await command_executor.shutdown()
            if temp_dir_obj:
                temp_dir_obj.cleanup()
                logger.info(f"Cleaned up temporary output storage path: {output_storage_path}")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


@main.command("filesystem-server")
@click.option(
    "--mode",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="Server mode: stdio (default), sse, or http"
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host address for SSE/HTTP modes (default: 127.0.0.1)"
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Port for SSE/HTTP modes (default: 8000)"
)
@click.option(
    "--path",
    default="/mcp",
    help="API endpoint path for HTTP mode (default: /mcp)"
)
@click.option(
    "--web-path",
    default="/web",
    help="Web interface path for SSE/HTTP modes (default: /web)"
)
def filesystem_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
):
    """Start the MCP Filesystem Server."""
    asyncio.run(_run_filesystem_server(
        mode, host, port, path, web_path
    ))


@main.command("unified-server")
@click.option(
    "--mode",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="Server mode: stdio (default), sse, or http"
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host address for SSE/HTTP modes (default: 127.0.0.1)"
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Port for SSE/HTTP modes (default: 8000)"
)
@click.option(
    "--path",
    default="/mcp",
    help="API endpoint path for HTTP mode (default: /mcp)"
)
@click.option(
    "--web-path",
    default="/web",
    help="Web interface path for SSE/HTTP modes (default: /web)"
)
@click.option(
    "--web-host",
    default="127.0.0.1",
    help="Host address for web management interface (default: 127.0.0.1)"
)
@click.option(
    "--web-port",
    type=int,
    default=get_random_available_port(),
    help="Port for web management interface (default: random available port)"
)
@click.option(
    "--enable-web-manager",
    is_flag=True,
    default=False,
    help="Enable web management interface"
)
@click.option(
    "--web-debug",
    is_flag=True,
    default=False,
    help="Use Flask development server for web interface (shows debug info)"
)
def unified_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    web_host: str,
    web_port: int,
    enable_web_manager: bool,
    web_debug: bool,
):
    """Start the MCP Unified Server with both command and filesystem capabilities."""
    asyncio.run(_run_unified_server(
        mode, host, port, path, web_path, 
        web_host, web_port, enable_web_manager, web_debug
    ))


if __name__ == "__main__":
    main()
