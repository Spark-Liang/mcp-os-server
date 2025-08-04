#!/usr/bin/env python3
"""
MCP OS Server

which includes a command server implementation, filesystem server implementation,
and web management interface.
"""

import logging
import os
import platform
import re
import socket
import sys
import tempfile
import webbrowser
from collections import defaultdict
from typing import Dict, List, Optional

import anyio
import click
from mcp.types import TextContent
from pydantic import BaseModel, Field

from .command.interfaces import IProcessManager
from .command.output_manager import OutputManager
from .command.process_manager_anyio import AnyioProcessManager
from .command.web_manager import WebManager
from .filesystem.filesystem_service import FilesystemService
from .filtered_fast_mcp import FilteredFastMCP

logger = logging.getLogger(__name__)


def setup_logger(mode: str, debug: bool = False) -> logging.Logger:
    """Setup logger based on server mode and debug flag."""
    logger = logging.getLogger("")

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # In stdio mode, minimize logging to avoid interfering with MCP protocol
    if mode == "stdio":
        # Still output to stderr to avoid stdout interference
        stream = sys.stderr
    else:
        stream = sys.stdout

    if debug:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handler = logging.StreamHandler(stream)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(threadName)s - %(name)s:%(lineno)d - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # 通过 LOG_FILE_PATH 配置文件路径
    log_file_path = os.getenv("LOG_FILE_PATH")
    if log_file_path:
        file_handler = logging.FileHandler(log_file_path, mode="w", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.setLevel(level)
    logger.debug(f"debug: {debug}")
    logger.info(f"log_file_path: {log_file_path}")
    logger.info(f"sys.getdefaultencoding(): {sys.getdefaultencoding()}")
    logger.info(f"sys.getfilesystemencoding(): {sys.getfilesystemencoding()}")

    return logging.getLogger(__name__)


def parse_allowed_commands(allowed_commands_str: str) -> List[str]:
    """Parse the ALLOWED_COMMANDS environment variable."""
    if not allowed_commands_str:
        return []

    # Split by comma and strip whitespace
    commands = [cmd.strip() for cmd in allowed_commands_str.split(",")]
    # Filter out empty strings
    return [cmd for cmd in commands if cmd]


def parse_allowed_directories(allowed_dirs_str: str) -> List[str]:
    """
    Parse the ALLOWED_DIRS environment variable.

    Example:
        ALLOWED_DIRS="C:\\Users\\23515\\AppData\\Local\\Temp,.\\"
    """
    if not allowed_dirs_str:
        return []

    # Split by comma and strip whitespace
    dirs = [dir_path.strip() for dir_path in allowed_dirs_str.split(",")]
    # Filter out empty strings
    return [dir_path for dir_path in dirs if dir_path]


class EnvVarsParseResult(BaseModel):
    """
    环境变量解析结果
    """

    command_default_encoding_map: Dict[str, str] = Field(description="命令默认编码映射")
    command_env_map: Dict[str, Dict[str, str]] = Field(description="命令环境变量映射")
    project_command_config_file: Optional[str] = Field(
        description="项目命令配置文件路径"
    )
    clean_envs: Dict[str, str] = Field(description="清理后的环境变量")


def parse_env_vars() -> EnvVarsParseResult:
    """
    解析环境变量
    """
    command_encoding_map = {}
    command_env_map = defaultdict(dict)
    clean_envs = {}
    command_encoding_prefix = "DEFAULT_ENCODING_"
    command_env_pattern = re.compile(r"^(.+?)_COMMAND_ENV_(.+?)$")
    project_command_config_file = None

    for env_key, env_value in os.environ.items():
        if env_key.startswith(command_encoding_prefix):
            command_name = env_key[len(command_encoding_prefix) :]
            if command_name and env_value.strip():
                command_encoding_map[command_name] = env_value.strip()
        elif command_env_pattern.match(env_key):
            match = command_env_pattern.match(env_key)
            if match:
                command_name = match.group(1).lower()
                var_name = match.group(2)
                command_env_map[command_name][var_name] = env_value
        elif env_key == "PROJECT_COMMAND_CONFIG_FILE":
            project_command_config_file = env_value
        else:
            clean_envs[env_key] = env_value

    logger.info(f"command_encoding_map: {command_encoding_map}")
    logger.info(f"command_env_map: {dict(command_env_map)}")
    logger.info(f"clean_envs: {clean_envs}")
    return EnvVarsParseResult(
        command_default_encoding_map=command_encoding_map,
        command_env_map=command_env_map,
        project_command_config_file=project_command_config_file,
        clean_envs=clean_envs,
    )


def parse_filesystem_service_features():
    """Parse the FILESYSTEM_SERVICE_FEATURES environment variable."""
    from .filesystem.models import FileSystemServiceFeature

    features_str = os.getenv("FILESYSTEM_SERVICE_FEATURES", "")
    if not features_str:
        return []

    # Split by comma and strip whitespace
    feature_names = [name.strip() for name in features_str.split(",")]
    # Filter out empty strings
    feature_names = [name for name in feature_names if name]

    features = []
    for feature_name in feature_names:
        try:
            # Try to match feature name to enum value
            for feature in FileSystemServiceFeature:
                if feature.value == feature_name or feature.name == feature_name:
                    features.append(feature)
                    break
            else:
                # If no match found, log a warning
                logger = logging.getLogger("mcp_os_server")
                logger.warning(f"Unknown filesystem service feature: {feature_name}")
        except Exception as e:
            logger = logging.getLogger("mcp_os_server")
            logger.error(
                f"Error parsing filesystem service feature '{feature_name}': {e}"
            )

    return features


def get_default_encoding() -> str:
    """Get the default encoding for the current platform."""
    if platform.system() == "Windows":
        return "gbk"
    else:
        return "utf-8"


async def create_process_manager(
    output_storage_path: str,
    process_retention_seconds: int,
    default_encoding: str,
) -> IProcessManager:
    """Create and initialize a ProcessManager instance."""
    # Create OutputManager
    output_manager = OutputManager(output_storage_path=output_storage_path)
    await output_manager.initialize()

    # Create ProcessManager based on the PROCESS_MANAGER_TYPE environment variable
    process_manager_type = os.environ.get("PROCESS_MANAGER_TYPE", "anyio")

    logger = logging.getLogger(__name__)
    logger.info(f"Creating process manager of type: {process_manager_type}")

    # For now, we only support anyio process manager
    logger.info("Using AnyioProcessManager")
    process_manager = AnyioProcessManager(
        output_manager=output_manager,
        process_retention_seconds=process_retention_seconds,
    )

    # Initialize the process manager
    await process_manager.initialize()

    return process_manager


async def create_web_manager(process_manager: IProcessManager) -> WebManager:
    """Create and initialize a WebManager instance."""
    web_manager = WebManager()
    await web_manager.initialize(process_manager)
    return web_manager


async def _run_filesystem_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    debug: bool,
):
    """Run the filesystem server in the specified mode."""
    # Setup logger based on mode
    logger = setup_logger(mode, debug)

    # Parse environment variables
    allowed_dirs_str = os.getenv("ALLOWED_DIRS", "")
    allowed_dirs = parse_allowed_directories(allowed_dirs_str)
    filesystem_service_features = parse_filesystem_service_features()

    if not allowed_dirs:
        logger.error(
            "Warning: No directories are allowed. Set ALLOWED_DIRS environment variable."
        )
        logger.error(
            "Example: ALLOWED_DIRS='/tmp,/home/user' mcp-os-server filesystem-server"
        )
        return

    # Only output initialization info in non-stdio modes
    logger.info("Starting MCP Filesystem Server in %s mode...", mode)
    logger.info("Allowed directories: %s", ", ".join(allowed_dirs))
    if filesystem_service_features:
        logger.info(
            "Filesystem service features: %s",
            ", ".join([f.name for f in filesystem_service_features]),
        )

    default_encoding = os.getenv("DEFAULT_ENCODING", get_default_encoding())
    logger.info(f"default_encoding: {default_encoding}")

    # Create filesystem server
    try:
        if not allowed_dirs:
            raise ValueError("至少需要指定一个允许的目录")

        filesystem_service = FilesystemService(features=filesystem_service_features)
        mcp = FilteredFastMCP(name="filesystem", version="0.1.0", host=host, port=port)

        from .filesystem.server import define_mcp_server

        define_mcp_server(
            mcp=mcp,
            filesystem_service=filesystem_service,
            allowed_dirs=allowed_dirs,
            default_encoding=default_encoding,
        )
    except Exception as e:
        logger.error("Failed to initialize filesystem server: %s", e, exc_info=True)
        return

    try:
        if mode == "stdio":
            # Run in stdio mode (default) - use async version
            await mcp.run_stdio_async()
        elif mode == "sse":
            # Run in SSE mode
            logger.info("Starting SSE server on %s:%s", host, port)
            logger.info(
                "MCP web interface available at: http://%s:%s%s", host, port, web_path
            )
            await mcp.run_sse_async()
        elif mode == "http":
            # Run in HTTP mode
            logger.info("Starting HTTP server on %s:%s", host, port)
            logger.info("MCP API endpoint: http://%s:%s%s", host, port, path)
            logger.info(
                "MCP web interface available at: http://%s:%s%s", host, port, web_path
            )
            await mcp.run_streamable_http_async()
    except KeyboardInterrupt:
        logger.info("Shutting down filesystem server...")
    except Exception as e:
        import traceback

        logger.error("Filesystem server error: %s", e, exc_info=True)
        logger.error("Traceback: %s", traceback.format_exc())


async def _run_unified_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    web_host: str,
    web_port: int,
    enable_web_manager: bool,
    debug: bool,
):
    """Run both command and filesystem servers in the specified mode."""
    # Setup logger based on mode
    logger = setup_logger(mode, debug)

    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None

    # Parse environment variables for command server
    allowed_commands_str = os.getenv("ALLOWED_COMMANDS", "")
    allowed_commands = parse_allowed_commands(allowed_commands_str)

    # Parse environment variables for filesystem server
    allowed_dirs_str = os.getenv("ALLOWED_DIRS", "")
    allowed_dirs = parse_allowed_directories(allowed_dirs_str)
    filesystem_service_features = parse_filesystem_service_features()

    if not allowed_commands and not allowed_dirs:
        logger.error("Warning: Neither commands nor directories are allowed.")
        logger.error("Set ALLOWED_COMMANDS and/or ALLOWED_DIRS environment variables.")
        logger.error(
            "Example: ALLOWED_COMMANDS='ls,cat,echo' ALLOWED_DIRS='/tmp,/home/user' mcp-os-server unified-server"
        )
        return

    process_retention_seconds = int(os.getenv("PROCESS_RETENTION_SECONDS", "300"))
    default_encoding = os.getenv("DEFAULT_ENCODING", get_default_encoding())
    env_vars_parse_result = parse_env_vars()

    # OUTPUT_STORAGE_PATH logic: Use temp dir if not explicitly set
    output_storage_path = os.getenv("OUTPUT_STORAGE_PATH")
    if output_storage_path is None:
        temp_dir_obj = tempfile.TemporaryDirectory()
        output_storage_path = temp_dir_obj.name
        logger.info("Using temporary output storage path: %s", output_storage_path)
    else:
        logger.info("Using provided output storage path: %s", output_storage_path)

    # Only output initialization info in non-stdio modes
    logger.info("Starting MCP Unified Server in %s mode...", mode)
    if allowed_commands:
        logger.info("Allowed commands: %s", ", ".join(allowed_commands))
        logger.info("Process retention: %s seconds", process_retention_seconds)
        logger.info("Default encoding: %s", default_encoding)
        if env_vars_parse_result.command_default_encoding_map:
            logger.info(
                "Command-specific encodings: %s",
                env_vars_parse_result.command_default_encoding_map,
            )
    if allowed_dirs:
        logger.info("Allowed directories: %s", ", ".join(allowed_dirs))
    if filesystem_service_features:
        logger.info(
            "Filesystem service features: %s",
            ", ".join([f.name for f in filesystem_service_features]),
        )

    # Create ProcessManager if commands are allowed
    process_manager = None
    if allowed_commands:
        try:
            default_timeout = int(os.getenv("DEFAULT_TIMEOUT", "15"))
            process_manager = await create_process_manager(
                output_storage_path=output_storage_path,
                process_retention_seconds=process_retention_seconds,
                default_encoding=default_encoding,
            )
        except Exception as e:
            logger.error("Failed to initialize process manager: %s", e, exc_info=True)
            return

    # Create and start WebManager if enabled
    web_manager = None
    if enable_web_manager and process_manager:
        try:
            web_manager = await create_web_manager(process_manager)
            await web_manager.start_web_interface(
                host=web_host, port=web_port, debug=debug
            )
            logger.info(
                "Web management interface available at: http://%s:%s",
                web_host,
                web_port,
            )
        except Exception as e:
            logger.error("Failed to start web manager: %s", e, exc_info=True)
            # Continue without web manager
            enable_web_manager = False

    # Create unified FastMCP instance
    mcp = FilteredFastMCP("mcp-unified-server", host=host, port=port)

    # Define command server tools if allowed
    if process_manager and allowed_commands:
        from .command.server import define_mcp_server

        define_mcp_server(
            mcp=mcp,
            process_manager=process_manager,
            allowed_commands=allowed_commands,
            default_encoding=default_encoding,
            command_default_encoding_map=env_vars_parse_result.command_default_encoding_map,
            default_timeout=default_timeout,
            command_env_map=env_vars_parse_result.command_env_map,
            default_envs=env_vars_parse_result.clean_envs,
            project_command_config_file=env_vars_parse_result.project_command_config_file,
        )

    # Define filesystem server tools if allowed
    if allowed_dirs:
        from .filesystem.filesystem_service import FilesystemService
        from .filesystem.server import define_mcp_server

        define_mcp_server(
            mcp=mcp,
            filesystem_service=FilesystemService(features=filesystem_service_features),
            allowed_dirs=allowed_dirs,
            default_encoding=default_encoding,
        )

    # Add command_open_web_manager tool if web manager is enabled
    if enable_web_manager:
        web_url = f"http://{web_host}:{web_port}"

        @mcp.tool(description=f"在浏览器中打开 Web 管理界面 {web_url}。")
        async def command_open_web_manager() -> List[TextContent]:
            """
            在浏览器中打开 Web 管理界面。
            """

            try:
                webbrowser.open(web_url)
                return [
                    TextContent(
                        type="text", text=f"已在浏览器中打开 Web 管理界面: {web_url} 🚀"
                    )
                ]
            except Exception as e:
                return [TextContent(type="text", text=f"打开 Web 管理界面失败: {e} ❌")]

        logger.info(
            "MCP tool 'command_open_web_manager' available to open: http://%s:%s",
            web_host,
            web_port,
        )

    try:
        if mode == "stdio":
            # Run in stdio mode (default) - use async version
            # Minimize logging in stdio mode to avoid protocol interference
            if enable_web_manager:
                # Only log web manager info to stderr as a critical notice
                logger.error(
                    "Web management interface running at: http://%s:%s",
                    web_host,
                    web_port,
                )
            await mcp.run_stdio_async()
        elif mode == "sse":
            # Run in SSE mode
            logger.info("Starting SSE server on %s:%s", host, port)
            logger.info(
                "MCP web interface available at: http://%s:%s%s", host, port, web_path
            )
            if enable_web_manager:
                logger.info(
                    "Process management interface available at: http://%s:%s",
                    web_host,
                    web_port,
                )
            await mcp.run_sse_async()
        elif mode == "http":
            # Run in HTTP mode
            logger.info("Starting HTTP server on %s:%s", host, port)
            logger.info("MCP API endpoint: http://%s:%s%s", host, port, path)
            logger.info(
                "MCP web interface available at: http://%s:%s%s", host, port, web_path
            )
            if enable_web_manager:
                logger.info(
                    "Process management interface available at: http://%s:%s",
                    web_host,
                    web_port,
                )
            await mcp.run_streamable_http_async()
    except KeyboardInterrupt:
        # Only log shutdown message in non-stdio modes
        logger.info("Shutting down unified server...")
    except Exception as e:
        import traceback

        logger.error("Unified server error: %s", e, exc_info=True)
        # Only log full traceback in non-stdio modes
        logger.error("Traceback: %s", traceback.format_exc())
    finally:
        # Cleanup
        try:
            if web_manager:
                await web_manager.shutdown()
            if process_manager:
                await process_manager.shutdown()
            if temp_dir_obj:
                temp_dir_obj.cleanup()
                logger.info(
                    "Cleaned up temporary output storage path: %s", output_storage_path
                )
        except Exception as e:
            logger.error("Error during cleanup: %s", e, exc_info=True)


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
    help="Server mode: stdio (default), sse, or http",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host address for SSE/HTTP modes (default: 127.0.0.1)",
)
@click.option(
    "--port", type=int, default=8000, help="Port for SSE/HTTP modes (default: 8000)"
)
@click.option(
    "--path", default="/mcp", help="API endpoint path for HTTP mode (default: /mcp)"
)
@click.option(
    "--web-path",
    default="/web",
    help="Web interface path for SSE/HTTP modes (default: /web)",
)
@click.option(
    "--web-host",
    default="127.0.0.1",
    help="Host address for web management interface (default: 127.0.0.1)",
)
@click.option(
    "--web-port",
    type=int,
    default=get_random_available_port(),
    help="Port for web management interface (default: random available port)",
)
@click.option(
    "--enable-web-manager",
    is_flag=True,
    default=False,
    help="Enable web management interface",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug mode (sets logging to DEBUG and uses Flask development server for web interface)",
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
    debug: bool,
):
    """Start the MCP Command Server with optional web management interface."""
    try:
        anyio.run(
            _run_command_server,
            mode,
            host,
            port,
            path,
            web_path,
            web_host,
            web_port,
            enable_web_manager,
            debug,
        )
        logger.info("Server exited")
    except BaseException as e:
        logger.error("Server exited with error: %s", e, exc_info=True)


async def _run_command_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    web_host: str,
    web_port: int,
    enable_web_manager: bool,
    debug: bool,
):
    """Run the command server in the specified mode."""
    # Setup logger based on mode
    logger = setup_logger(mode, debug)

    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None

    # Parse environment variables
    allowed_commands_str = os.getenv("ALLOWED_COMMANDS", "")
    allowed_commands = parse_allowed_commands(allowed_commands_str)

    if not allowed_commands:
        logger.error(
            "Warning: No commands are allowed. Set ALLOWED_COMMANDS environment variable."
        )
        logger.error(
            "Example: ALLOWED_COMMANDS='ls,cat,echo' mcp-os-server command-server"
        )
        return

    process_retention_seconds = int(os.getenv("PROCESS_RETENTION_SECONDS", "300"))
    default_encoding = os.getenv("DEFAULT_ENCODING", get_default_encoding())
    env_vars_parse_result = parse_env_vars()

    # OUTPUT_STORAGE_PATH logic: Use temp dir if not explicitly set
    output_storage_path = os.getenv("OUTPUT_STORAGE_PATH")
    if output_storage_path is None:
        temp_dir_obj = tempfile.TemporaryDirectory()
        output_storage_path = temp_dir_obj.name
        logger.info("Using temporary output storage path: %s", output_storage_path)
    else:
        logger.info("Using provided output storage path: %s", output_storage_path)

    # Only output initialization info in non-stdio modes
    logger.info("Starting MCP Command Server in %s mode...", mode)
    logger.info("Allowed commands: %s", ", ".join(allowed_commands))
    logger.info("Process retention: %s seconds", process_retention_seconds)
    logger.info("Default encoding: %s", default_encoding)
    if env_vars_parse_result.command_default_encoding_map:
        logger.info(
            "Command-specific encodings: %s",
            env_vars_parse_result.command_default_encoding_map,
        )

    # Create CommandExecutor
    try:
        default_timeout = int(os.getenv("DEFAULT_TIMEOUT", "15"))
        process_manager = await create_process_manager(
            output_storage_path=output_storage_path,
            process_retention_seconds=process_retention_seconds,
            default_encoding=default_encoding,
        )
    except Exception as e:
        logger.error("Failed to initialize command executor: %s", e, exc_info=True)
        return

    # Create and start WebManager if enabled
    web_manager = None
    if enable_web_manager:
        try:
            web_manager = await create_web_manager(process_manager)
            await web_manager.start_web_interface(
                host=web_host, port=web_port, debug=debug
            )
            logger.info(
                "Web management interface available at: http://%s:%s",
                web_host,
                web_port,
            )
        except Exception as e:
            logger.error("Failed to start web manager: %s", e, exc_info=True)
            # Continue without web manager
            enable_web_manager = False

    # Create FastMCP instance
    mcp = FilteredFastMCP("mcp-command-server", host=host, port=port)

    # Define MCP tools
    from .command.server import define_mcp_server

    define_mcp_server(
        mcp=mcp,
        process_manager=process_manager,
        allowed_commands=allowed_commands,
        default_encoding=default_encoding,
        default_timeout=default_timeout,
        command_default_encoding_map=env_vars_parse_result.command_default_encoding_map,
        command_env_map=env_vars_parse_result.command_env_map,
        default_envs=env_vars_parse_result.clean_envs,
        project_command_config_file=env_vars_parse_result.project_command_config_file,
    )

    # Add command_open_web_manager tool if web manager is enabled
    if enable_web_manager:
        web_url = f"http://{web_host}:{web_port}"

        @mcp.tool(description=f"在浏览器中打开 Web 管理界面 {web_url}。")
        async def command_open_web_manager() -> List[TextContent]:
            """
            在浏览器中打开 Web 管理界面。
            """

            try:
                webbrowser.open(web_url)
                return [
                    TextContent(
                        type="text", text=f"已在浏览器中打开 Web 管理界面: {web_url} 🚀"
                    )
                ]
            except Exception as e:
                return [TextContent(type="text", text=f"打开 Web 管理界面失败: {e} ❌")]

        logger.info(
            "MCP tool 'command_open_web_manager' available to open: http://%s:%s",
            web_host,
            web_port,
        )

    try:
        if mode == "stdio":
            # Run in stdio mode (default) - use async version
            # Minimize logging in stdio mode to avoid protocol interference
            if enable_web_manager:
                # Only log web manager info to stderr as a critical notice
                logger.error(
                    "Web management interface running at: http://%s:%s",
                    web_host,
                    web_port,
                )
            await mcp.run_stdio_async()
        elif mode == "sse":
            # Run in SSE mode
            logger.info("Starting SSE server on %s:%s", host, port)
            logger.info(
                "MCP web interface available at: http://%s:%s%s", host, port, web_path
            )
            if enable_web_manager:
                logger.info(
                    "Process management interface available at: http://%s:%s",
                    web_host,
                    web_port,
                )
            await mcp.run_sse_async()
        elif mode == "http":
            # Run in HTTP mode
            logger.info("Starting HTTP server on %s:%s", host, port)
            logger.info("MCP API endpoint: http://%s:%s%s", host, port, path)
            logger.info(
                "MCP web interface available at: http://%s:%s%s", host, port, web_path
            )
            if enable_web_manager:
                logger.info(
                    "Process management interface available at: http://%s:%s",
                    web_host,
                    web_port,
                )
            await mcp.run_streamable_http_async()
    except KeyboardInterrupt:
        # Only log shutdown message in non-stdio modes
        logger.info("Shutting down server...")
    except Exception as e:
        import traceback

        logger.error("Server error: %s", e, exc_info=True)
        # Only log full traceback in non-stdio modes
        logger.error("Traceback: %s", traceback.format_exc())
    finally:
        # Cleanup
        try:
            if web_manager:
                await web_manager.shutdown()
            if process_manager:
                await process_manager.shutdown()
            if temp_dir_obj:
                temp_dir_obj.cleanup()
                logger.info(
                    "Cleaned up temporary output storage path: %s", output_storage_path
                )
        except Exception as e:
            logger.error("Error during cleanup: %s", e, exc_info=True)


@main.command("filesystem-server")
@click.option(
    "--mode",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="Server mode: stdio (default), sse, or http",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host address for SSE/HTTP modes (default: 127.0.0.1)",
)
@click.option(
    "--port", type=int, default=8000, help="Port for SSE/HTTP modes (default: 8000)"
)
@click.option(
    "--path", default="/mcp", help="API endpoint path for HTTP mode (default: /mcp)"
)
@click.option(
    "--web-path",
    default="/web",
    help="Web interface path for SSE/HTTP modes (default: /web)",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug mode (sets logging to DEBUG and uses Flask development server for web interface)",
)
def filesystem_server(
    mode: str,
    host: str,
    port: int,
    path: str,
    web_path: str,
    debug: bool,
):
    """Start the MCP Filesystem Server."""
    try:
        anyio.run(
            _run_filesystem_server,
            mode,
            host,
            port,
            path,
            web_path,
            debug,
        )
        logger.info("Server exited")
    except BaseException as e:
        logger.error("Server exited with error: %s", e, exc_info=True)


@main.command("unified-server")
@click.option(
    "--mode",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="Server mode: stdio (default), sse, or http",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host address for SSE/HTTP modes (default: 127.0.0.1)",
)
@click.option(
    "--port", type=int, default=8000, help="Port for SSE/HTTP modes (default: 8000)"
)
@click.option(
    "--path", default="/mcp", help="API endpoint path for HTTP mode (default: /mcp)"
)
@click.option(
    "--web-path",
    default="/web",
    help="Web interface path for SSE/HTTP modes (default: /web)",
)
@click.option(
    "--web-host",
    default="127.0.0.1",
    help="Host address for web management interface (default: 127.0.0.1)",
)
@click.option(
    "--web-port",
    type=int,
    default=get_random_available_port(),
    help="Port for web management interface (default: random available port)",
)
@click.option(
    "--enable-web-manager",
    is_flag=True,
    default=False,
    help="Enable web management interface",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug mode (sets logging to DEBUG and uses Flask development server for web interface)",
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
    debug: bool,
):
    """Start the MCP Unified Server with both command and filesystem capabilities."""
    try:
        anyio.run(
            _run_unified_server,
            mode,
            host,
            port,
            path,
            web_path,
            web_host,
            web_port,
            enable_web_manager,
            debug,
        )
        logger.info("Server exited")
    except BaseException as e:
        logger.error("Server exited with error: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
