import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence
import urllib.parse
from pathlib import Path
import os
import sys
import json
import anyio
from mcp.server.session import ServerSession
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import TextContent
from pydantic import Field, BaseModel
from dotenv import dotenv_values
import yaml
from dataclasses import dataclass, field
import functools

from .exceptions import (
    CommandExecutionError,
    ProcessControlError,
    ProcessNotFoundError,
    ProcessTimeoutError,
)
from .interfaces import IProcessManager
from .models import ProcessStatus
from mcp_os_server.path_utils import list_roots, try_resolve_win_path_in_url_format, resolve_paths_and_check_allowed, resolve_path_and_check_allowed

logger = logging.getLogger(__name__)


async def _collect_logs(async_gen):
    return [item async for item in async_gen]


async def _apply_grep_filter(log_entries, grep_pattern, grep_mode):
    """Apply grep filtering to log entries."""
    if not grep_pattern:
        return log_entries

    try:
        pattern = re.compile(grep_pattern)
        filtered_entries = []

        for entry in log_entries:
            if grep_mode == "content":
                # Return only the matching content itself
                matches = pattern.findall(entry.text)
                for match in matches:
                    # Create a new entry with just the matched content
                    filtered_entry = type(entry)(
                        timestamp=entry.timestamp,
                        text=match,
                        output_key=entry.output_key,
                    )
                    filtered_entries.append(filtered_entry)
            else:  # grep_mode == "line" (default)
                # Return the entire line if it matches
                if pattern.search(entry.text):
                    filtered_entries.append(entry)

        return filtered_entries
    except re.error:
        # If regex is invalid, return original entries
        return log_entries



def define_mcp_server(
    mcp: FastMCP,
    process_manager: IProcessManager,
    *,
    allowed_commands: List[str],
    default_encoding: str,
    command_default_encoding_map: Dict[str, str],
    default_timeout: int,
    command_env_map: Dict[str, Dict[str, str]] = {},
    default_envs: Dict[str, str] = {},
    project_command_config_file: Optional[str] = None,
    # TODO: add other options if needed
) -> None:
    """
    Defines MCP tools for command execution and process management.

    Args:
        mcp: The MCP server instance.
        process_manager: The process manager instance.
        allowed_commands: A list of commands that are allowed to be executed.
        default_encoding: The default encoding for the command output.
        default_timeout: The default timeout for command execution.
        command_env_map: A map of commands to their default environment variables.
        default_envs: default environment variables for all commands.
        project_command_config_file: The path to the project command config file.
    """

    logger.info("Allowed commands: %s", allowed_commands)
    logger.info("Default encoding: %s", default_encoding)
    logger.info("Default timeout: %s", default_timeout)
    logger.info("Command default encoding map: %s", command_default_encoding_map)
    logger.info("Command env map: %s", command_env_map)
    logger.info("Default environment variables: %s", default_envs)
    logger.info("Project command config file: %s", project_command_config_file)

    @dataclass
    class CommandConfig:
        """Command configuration.

        Fields:
        - default_encoding: 默认编码 (Optional[str])
        - default_timeout: 默认超时时间 (Optional[int])
        - default_envs: 默认环境变量 (Dict[str, str | None])
        """

        default_encoding: Optional[str] = None
        default_timeout: Optional[int] = None
        default_envs: Dict[str, str | None] = field(default_factory=dict)

    @dataclass
    class ProjectCommandConfig:
        """Project command configuration.

        Fields:
        - extra_paths: 额外路径 (Optional[List[Path]])
        - commands: 命令配置 (Dict[str, CommandConfig])
        """

        extra_paths: Optional[List[Path]] = field(default_factory=list)
        commands: Dict[str, CommandConfig] = field(default_factory=dict)


    @dataclass
    class ResolvedStartProcessParams:
        """解析后的启动进程参数

        Fields:
        - command: 要执行的命令 (str)
        - directory: 工作目录 (str)
        - timeout: 命令执行超时时间，单位：秒 (int)
        - envs: 启动进程时设置的环境变量 (Dict[str, str])
        - encoding: 命令输出编码 (str)
        - args: 命令的参数列表 (Optional[List[str]])
        - extra_paths: 额外添加到 PATH 环境变量中的路径 (Optional[List[Path]])
        """

        command: str
        directory: str
        timeout: int
        envs: Dict[str, str]
        encoding: str
        args: Optional[List[str]] = None
        extra_paths: Optional[List[Path]] = None

    async def load_project_command_config(
        context: Context,
        directory: Path,
    ) -> Optional[ProjectCommandConfig]:
        """
        Load project command configuration from YAML file.
        Returns None if no config file is found or can't be loaded.
        """
        if not project_command_config_file:
            return None
            
        try:
            directory_path = directory
            root_info_items = await list_roots(context)
            
            for root_info_item in root_info_items:
                root_path = root_info_item.local_path
                if not root_path:
                    continue
                if directory_path.is_relative_to(root_path):
                    config_file_path = (root_path / project_command_config_file).resolve()
                    logger.debug("Checking config file: %s", config_file_path)
                    
                    if config_file_path.exists() and config_file_path.is_file():
                        logger.debug("Loading config from: %s", config_file_path)
                        with open(config_file_path, 'r', encoding='utf-8') as f:
                            config = yaml.safe_load(f)
                        logger.debug("Loaded config: %s", config)

                        config_model = ProjectCommandConfig(**{
                            'extra_paths': [],
                            'commands': {},
                        })
                        # 将 config 中的extra_paths 转换为绝对路径
                        if 'extra_paths' in config:
                            original_extra_paths = config['extra_paths']
                            new_extra_paths = []
                            for path_str in original_extra_paths:
                                path = Path(path_str)
                                if path.is_absolute():
                                    new_extra_paths.append(str(path))
                                else:
                                    new_extra_paths.append(str((root_path / path).resolve()))
                            config_model.extra_paths = new_extra_paths

                        if 'commands' in config:
                            config_model.commands = {}
                            for command_name, command_config in config['commands'].items():
                                config_model.commands[command_name] = CommandConfig(**{
                                    'default_encoding': command_config.get('default_encoding'),
                                    'default_timeout': command_config.get('default_timeout'),
                                    'default_envs': command_config.get('default_envs', {}),
                                })

                        logger.debug("ProjectCommandConfig: %s", config_model)
                        return config_model
                        
        except Exception as e:
            logger.warning("Failed to load project command config: %s", e)
            logger.debug("详细错误信息: ",exc_info=True)
            
        return None

    async def resolve_start_process_params(
        context: Context,
        command: str, 
        directory: str,
        args: Optional[List[str]] = None,
        envs: Optional[Dict[str, str | None]] = None,
        encoding: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> ResolvedStartProcessParams:
        """
        解析启动进程参数，并返回解析后的参数。
        解析规则：
        - directory: 如果是相对路径，则需要使用 resolve_path_and_check_allowed 解析为绝对路径。
        - envs: 
            - 优先级：
                - 参数直接传入的环境变量
                - 项目环境变量
                - 特定命令全局默认环境变量
                - 默认环境变量
            - 合并规则：如果优先级高的key存在，则优先级低的key不生效。比如高优先级的 key 为 None ，相当于删除低优先级的key。
        - encoding:
            - 优先级：
                - 参数直接传入的编码
                - 项目特定命令默认编码
                - 特定命令全局默认编码
                - 默认编码
        - timeout:
            - 优先级：
                - 参数直接传入的超时时间
                - 项目特定命令默认超时时间
                - 默认超时时间
        - extra_paths: 从项目命令配置文件中获取。
                
        Args:
            context: The context of the command.
            command: The command to execute.
            directory: The working directory for the command.
            envs: The environment variables for the command.
            encoding: The encoding for the command output.
            timeout: The timeout for the command execution.
        """
        logger.debug("resolve_start_process_params: %s, %s, %s, %s, %s, %s", context, command, directory, args, envs, encoding, timeout)
        # Resolve directory (for command execution, we don't restrict allowed dirs)
        directory_path = await resolve_path_and_check_allowed(
            directory, context=context
        );
        
        # Load project command config
        project_config = await load_project_command_config(context, directory_path)
        logger.debug("Project config: %s", project_config)
        
        # Get command-specific config from project
        command_config = None
        if project_config and project_config.commands:
            # Case-insensitive command matching
            for cmd_name, cmd_config in project_config.commands.items():
                if cmd_name.lower() == command.lower():
                    command_config = cmd_config or {}
                    break
        logger.debug("Command config: %s", command_config)
        # Resolve encoding with priority
        resolved_encoding = encoding
        if not resolved_encoding and command_config and command_config.default_encoding:
            resolved_encoding = command_config.default_encoding
        if not resolved_encoding:
            # Check command_default_encoding_map
            for key, value in command_default_encoding_map.items():
                if command.upper() == key.upper():
                    resolved_encoding = value
                    break
        if not resolved_encoding:
            resolved_encoding = default_encoding
        
        # Resolve timeout with priority  
        resolved_timeout = timeout
        if resolved_timeout is None and command_config and command_config.default_timeout:
            resolved_timeout = command_config.default_timeout
        if resolved_timeout is None:
            resolved_timeout = default_timeout
        
        # Resolve environment variables with priority
        # Start with default environment variables
        resolved_envs: Dict[str, str | None] = {k:v for k,v in default_envs.items()}
        
        # Add command-specific environment variables
        if command in command_env_map:
            for key, value in command_env_map[command].items():
                resolved_envs[key] = value
        
        # Add project environment variables
        project_envs = command_config.default_envs if command_config and command_config.default_envs else {}
        for key, value in project_envs.items():
            if value == "":  # Empty string means delete
                if key in resolved_envs:
                    del resolved_envs[key]
            else:
                resolved_envs[key] = value
        
        # Add user-provided environment variables (highest priority)
        if envs:
            for key, value in envs.items():
                if value is None:  # None means delete
                    if key in resolved_envs:
                        del resolved_envs[key]
                else:
                    resolved_envs[key] = value
        
        return ResolvedStartProcessParams(
            command=command,
            args=args,
            directory=str(directory_path),
            timeout=resolved_timeout,
            envs={k: v for k, v in resolved_envs.items() if v is not None} if resolved_envs else {},
            encoding=resolved_encoding,
            extra_paths=project_config.extra_paths if project_config and project_config.extra_paths else None,
        )

    def is_json_string_list(s):
        """
        判断一个字符串是否为有效的 JSON 字符串，并且解析后是一个列表。

        Args:
            s: 要检查的字符串。

        Returns:
            如果字符串是有效的 JSON 字符串且解析后是列表，则返回 True；否则返回 False。
        """
        try:
            parsed_json = json.loads(s)
            return isinstance(parsed_json, list)
        except json.JSONDecodeError:
            # 如果字符串不是有效的 JSON 格式，会捕获此异常
            return False
        except TypeError:
            # 如果传入的不是字符串类型，也会捕获此异常
            return False

    def standardize_args(args: Optional[List[str] | str]) -> Optional[List[str]]:
        if isinstance(args, str):
            if is_json_string_list(args):
                return json.loads(args)
            else:
                raise ValueError(f"Invalid args string!!! must be a valid JSON list with all string elements: {args}")
        elif isinstance(args, list):
            return args
        else:
            if args is None:
                return None
            else:
                raise ValueError(f"Invalid args type!!! must be a list[str] or str: {args}")
        
    def auto_handle_exception(func):
        """
        自动处理异常，将异常转换为 TextContent 对象
        
        Args:
            func: 要自动处理异常的函数
            
        Returns:
            TextContent 对象
        """
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error("Error executing tool %s: %s", func.__name__, e, exc_info=True)
                return TextContent(text=f"Error executing tool {func.__name__}: {str(e)}", type="text")
        return wrapper
    
    @mcp.tool()
    @auto_handle_exception
    async def command_execute(
        context: Context,
        command: str = Field(
            description="The command to execute. Only allowed commands are: "
            + ", ".join(allowed_commands)
        ),
        args: Optional[List[str] | str] = Field(
            None, description="The argument list for the command. **type: list[str] | None**"
        ),
        directory: str = Field(
            description="The working directory (absolute path) for the command."
        ),
        stdin: Optional[str] = Field(
            None, description="Input to pass to the command via stdin."
        ),
        timeout: float = Field(
            default_timeout, description="Maximum execution time in seconds."
        ),
        envs: Optional[Dict[str, str | None]] = Field(
            None, description="Additional environment variables for the command. None means delete the environment variable."
        ),
        encoding: str = Field(
            default_encoding,
            description="Character encoding for the command output (e.g., 'utf-8', 'gbk', 'cp936').",
        ),
        limit_lines: float = Field(
            500,
            description="Maximum number of lines to return in each TextContent.",
            gt=0,
        ),
    ) -> Sequence[TextContent]:
        """
        Executes a single shell command and returns the result.
        This tool only supports simple commands in the form of `command + args` and does not parse complex shell operators.
        """
        # Check if command is allowed
        if command not in allowed_commands:
            return [
                TextContent(
                    type="text",
                    text=f"Command '{command}' is not allowed. Allowed commands: {', '.join(allowed_commands)}",
                )
            ]

        # Convert float parameters to int for internal use
        timeout_int = max(1, int(timeout)) if timeout else 15
        limit_lines_int = max(1, int(limit_lines)) if limit_lines else 500

        args_list: Optional[List[str]] = standardize_args(args)

        try:
            # Resolve all process parameters
            resolved_params = await resolve_start_process_params(
                context, command, directory, args_list, envs, encoding, timeout_int
            )
            
            process = await process_manager.start_process(
                command=[resolved_params.command] + (resolved_params.args or []),
                directory=resolved_params.directory,
                description=f"Synchronous execution: {command}",
                stdin_data=stdin,
                timeout=resolved_params.timeout,
                envs=resolved_params.envs,
                encoding=resolved_params.encoding,
                extra_paths=[ p for p in resolved_params.extra_paths ] if resolved_params.extra_paths else None,
            )

            logger.info("Waiting for process completion: %s", process.pid)
            info = await process.wait_for_completion()
            logger.info("Process completed: %s", process.pid)

            try:
                stdout_lines = [
                    entry.text async for entry in process.get_output("stdout")
                ]
                stderr_lines = [
                    entry.text async for entry in process.get_output("stderr")
                ]
                stdout = "\n".join(stdout_lines)
                stderr = "\n".join(stderr_lines)
            except ProcessNotFoundError:
                stdout = ""
                stderr = ""

            # Always return 3 TextContent items as per FDS specification
            return [
                TextContent(type="text", text=f"**process {process.pid} end with {info.status.value} (exit code: {info.exit_code})**"),
                TextContent(type="text", text=f"---\nstdout:\n---\n{stdout}\n"),
                TextContent(type="text", text=f"---\nstderr:\n---\n{stderr}\n"),
            ]
        except ProcessTimeoutError:
            # Collect partial outputs
            try:
                stdout_lines = [
                    entry.text async for entry in process.get_output("stdout")
                ]
                stderr_lines = [
                    entry.text async for entry in process.get_output("stderr")
                ]
                stdout = "\n".join(stdout_lines)
                stderr = "\n".join(stderr_lines)
            except ProcessNotFoundError:
                stdout = ""
                stderr = ""

            # 超时时返回4个TextContent，类似正常执行但包含额外的指导信息
            return [
                TextContent(
                    type="text", text=f"**Command timed out with PID: {process.pid}**"
                ),
                TextContent(
                    type="text", text=f"---\nstdout (partial):\n---\n{stdout}\n"
                ),
                TextContent(
                    type="text", text=f"---\nstderr (partial):\n---\n{stderr}\n"
                ),
                TextContent(
                    type="text",
                    text=f"**Note: Process {process.pid} might still be running. Use `command_ps_logs` to view continued output. Or use `timeout` to set a longer timeout.**",
                ),
            ]
        except CommandExecutionError as e:
            return [TextContent(type="text", text=f"Command execution failed: {e}")]
        except Exception as e:
            logger.error("Unexpected error during command execution: %s", e, exc_info=True)
            # Catch any unexpected exceptions to prevent MCP protocol stack crashes
            import traceback

            error_details = traceback.format_exc()
            return [
                TextContent(
                    type="text",
                    text=f"Unexpected error during command execution: {e}\nDetails: {error_details[:500]}...",
                )
            ]

    @mcp.tool()
    @auto_handle_exception
    async def command_bg_start(
        context: Context,
        command: str = Field(
            description="The command to execute. Only allowed commands are: "
            + ", ".join(allowed_commands)
        ),
        args: Optional[List[str] | str] = Field(
            None, description="The argument list for the command. **type: list[str] | None**"
        ),
        directory: str = Field(
            description="The working directory (absolute path) for the command."
        ),
        description: str = Field(description="A description for the command."),
        labels: Optional[List[str]] = Field(
            None, description="Labels to categorize the command."
        ),
        stdin: Optional[str] = Field(
            None, description="Input to pass to the command via stdin."
        ),
        envs: Optional[Dict[str, str | None]] = Field(
            None, description="Additional environment variables for the command. None means delete the environment variable."
        ),
        encoding: str = Field(
            default_encoding, description="Character encoding for the command output."
        ),
        timeout: float = Field(
            default_timeout, description="Maximum execution time in seconds."
        ),
    ) -> Sequence[TextContent]:
        """
        Starts a background process for a single command and provides fine-grained management.
        """
        # Check if command is allowed
        if command not in allowed_commands:
            return [
                TextContent(
                    type="text",
                    text=f"Command '{command}' is not allowed. Allowed commands: {', '.join(allowed_commands)}",
                )
            ]

        # Convert float parameter to int for internal use
        timeout_int = max(1, int(timeout)) if timeout else 15

        args_list: Optional[List[str]] = standardize_args(args)

        try:
            # Resolve all process parameters
            resolved_params = await resolve_start_process_params(
                context, command, directory, args_list, envs, encoding, timeout_int
            )
            
            process = await process_manager.start_process(
                command=[resolved_params.command] + (resolved_params.args or []),
                directory=resolved_params.directory,
                description=description,
                stdin_data=stdin,
                timeout=resolved_params.timeout,
                envs=resolved_params.envs,
                encoding=resolved_params.encoding,
                labels=labels,
                extra_paths=[ p for p in resolved_params.extra_paths ] if resolved_params.extra_paths else None,
            )
            return [
                TextContent(
                    type="text", text=f"Process started with PID: {process.pid}"
                )
            ]
        except CommandExecutionError as e:
            return [
                TextContent(
                    type="text", text=f"Failed to start background process: {e}"
                )
            ]

    @mcp.tool()
    @auto_handle_exception
    async def command_ps_list(
        labels: Optional[List[str]] = Field(
            None, description="Filter processes by labels."
        ),
        status: Optional[str] = Field(
            None,
            description="Filter by status ('running', 'completed', 'failed', 'terminated', 'error').",
        ),
    ) -> Sequence[TextContent]:
        """
        Lists running or completed background processes.
        """
        process_status = None
        if status:
            try:
                process_status = ProcessStatus(status)
            except ValueError:
                return [
                    TextContent(
                        type="text",
                        text=f"Invalid status: {status}. Must be one of {', '.join([s.value for s in ProcessStatus])}",
                    )
                ]

        processes = await process_manager.list_processes(
            status=process_status, labels=labels
        )
        if not processes:
            return [TextContent(type="text", text="No processes found.")]

        # Format the process list into a markdown table
        header = "| PID | Status | Command | Description | Labels |"
        separator = "|---|---|---|---|---|"
        rows = [header, separator]
        for p in processes:
            command_str = " ".join(p.command)
            labels_str = ", ".join(p.labels) if p.labels else "N/A"
            rows.append(
                f"| {p.pid[:8]} | {p.status.value} | `{command_str}` | {p.description} | {labels_str} |"
            )

        return [TextContent(type="text", text="\n".join(rows))]

    @mcp.tool()
    @auto_handle_exception
    async def command_ps_stop(
        pid: str = Field(description="The ID of the process to stop."),
        force: Optional[bool] = Field(
            False, description="Whether to force stop the process (default: false)."
        ),
    ) -> Sequence[TextContent]:
        """
        Stops a running process.
        """
        try:
            await process_manager.stop_process(pid, force or False)
            return [TextContent(type="text", text=f"Process {pid} stopped.")]
        except ProcessNotFoundError as e:
            return [TextContent(type="text", text=str(e))]
        except ProcessControlError as e:
            return [TextContent(type="text", text=f"Error stopping process {pid}: {e}")]

    @mcp.tool()
    @auto_handle_exception
    async def command_ps_logs(
        pid: str = Field(description="The ID of the process to get output from."),
        tail: Optional[float] = Field(
            None, description="Number of lines to show from the end."
        ),
        since: Optional[str] = Field(
            None,
            description="Show logs since this timestamp (ISO format, e.g., '2023-05-06T14:30:00').",
        ),
        until: Optional[str] = Field(
            None,
            description="Show logs until this timestamp (ISO format, e.g., '2023-05-06T15:30:00').",
        ),
        with_stdout: Optional[bool] = Field(True, description="Show standard output."),
        with_stderr: Optional[bool] = Field(False, description="Show error output."),
        add_time_prefix: Optional[bool] = Field(
            True, description="Add a timestamp prefix to each output line."
        ),
        time_prefix_format: Optional[str] = Field(
            None, description="Format of the timestamp prefix, using strftime format."
        ),
        follow_seconds: Optional[float] = Field(
            None,
            description="Wait for the specified number of seconds to get new logs. If 0, or None, return immediately.",
        ),
        limit_lines: float = Field(
            500,
            description="Maximum number of lines to return in each TextContent.",
            gt=0,
        ),
        grep: Optional[str] = Field(
            None, description="Perl standard regular expression to filter output."
        ),
        grep_mode: Optional[str] = Field(
            "line",
            description="Regex match mode: 'line' (matching line) or 'content' (matching content itself).",
        ),
    ) -> Sequence[TextContent]:
        """
        Gets the output of a process, with support for filtering via regular expressions.
        """
        # Convert float parameters to int for internal use
        tail_int = max(1, int(tail)) if tail is not None and tail > 0 else None
        follow_seconds_int = (
            max(0, int(follow_seconds)) if follow_seconds is not None else None
        )
        limit_lines_int = max(1, int(limit_lines)) if limit_lines else 500
        try:
            since_ts = datetime.fromisoformat(since).timestamp() if since else None
        except (ValueError, TypeError):
            return [
                TextContent(
                    type="text",
                    text=f"Invalid 'since' timestamp format: {since}. Expected ISO format (e.g., '2023-05-06T14:30:00').",
                )
            ]

        try:
            until_ts = datetime.fromisoformat(until).timestamp() if until else None
        except (ValueError, TypeError):
            return [
                TextContent(
                    type="text",
                    text=f"Invalid 'until' timestamp format: {until}. Expected ISO format (e.g., '2023-05-06T15:30:00').",
                )
            ]

        try:
            # First, get process details for the header
            process = await process_manager.get_process(pid)
            process_info = await process.get_details()

            # Create the process info header
            command_str = " ".join(process_info.command)
            status_description = (
                "进程仍在运行"
                if process_info.status == "running"
                else f"进程已{process_info.status}"
            )

            process_header = (
                f"**进程{pid}（状态：{process_info.status}）**\n"
                f"命令: {command_str}\n"
                f"描述: {process_info.description}\n"
                f"状态: {status_description}"
            )

            result_contents = [TextContent(type="text", text=process_header)]

            # Handle time prefix format
            time_format = time_prefix_format or "%Y-%m-%d %H:%M:%S.%f"

            # Process stdout if requested
            if with_stdout:
                try:
                    stdout_logs = []
                    async for log_entry in process.get_output(
                        "stdout", since_ts, until_ts, tail_int
                    ):
                        stdout_logs.append(log_entry)

                    if stdout_logs:
                        # Apply grep filtering if specified
                        filtered_logs = await _apply_grep_filter(
                            stdout_logs, grep, grep_mode
                        )

                        # Format logs with time prefix if requested
                        formatted_logs = []
                        for log in filtered_logs[:limit_lines_int]:
                            if add_time_prefix:
                                time_str = log.timestamp.strftime(time_format)
                                formatted_logs.append(f"[{time_str}] {log.text}")
                            else:
                                formatted_logs.append(log.text)

                        if formatted_logs:
                            stdout_content = (
                                "---\nstdout: 匹配内容（根据grep_mode）\n---\n"
                                + "\n".join(formatted_logs)
                            )
                            result_contents.append(
                                TextContent(type="text", text=stdout_content)
                            )
                except Exception:
                    # If there's an error getting stdout, continue to stderr
                    pass

            # Process stderr if requested
            if with_stderr:
                try:
                    stderr_logs = []
                    async for log_entry in process.get_output(
                        "stderr", since_ts, until_ts, tail_int
                    ):
                        stderr_logs.append(log_entry)

                    if stderr_logs:
                        # Apply grep filtering if specified
                        filtered_logs = await _apply_grep_filter(
                            stderr_logs, grep, grep_mode
                        )

                        # Format logs with time prefix if requested
                        formatted_logs = []
                        for log in filtered_logs[:limit_lines_int]:
                            if add_time_prefix:
                                time_str = log.timestamp.strftime(time_format)
                                formatted_logs.append(f"[{time_str}] {log.text}")
                            else:
                                formatted_logs.append(log.text)

                        if formatted_logs:
                            stderr_content = (
                                "---\nstderr: 匹配内容（根据grep_mode）\n---\n"
                                + "\n".join(formatted_logs)
                            )
                            result_contents.append(
                                TextContent(type="text", text=stderr_content)
                            )
                except Exception:
                    # If there's an error getting stderr, continue
                    pass

            # Handle follow_seconds - simulate waiting for new logs
            if follow_seconds_int and follow_seconds_int > 0:
                await anyio.sleep(
                    min(follow_seconds_int, 5)
                )  # Cap at 5 seconds for safety

            # If no stdout/stderr was requested or found, add appropriate message
            if not with_stdout and not with_stderr:
                return [TextContent(type="text", text="No logs requested.")]
            elif len(result_contents) == 1:  # Only header, no logs found
                return [TextContent(type="text", text="No logs found.")]

            return result_contents

        except ProcessNotFoundError:
            return [TextContent(type="text", text=f"Process with ID {pid} not found")]
        except Exception as e:
            return [TextContent(type="text", text=f"An error occurred: {e}")]

    @mcp.tool()
    @auto_handle_exception
    async def command_ps_clean(
        pids: List[str] = Field(description="A list of process IDs to clean."),
    ) -> Sequence[TextContent]:
        """
        Cleans up completed or failed processes.
        """
        if not pids:
            return [TextContent(type="text", text="No process IDs provided.")]
        try:
            results = await process_manager.clean_processes(pids)
            # Format the results into a readable string
            formatted_results = "\n".join(
                [f"  - {pid}: {result}" for pid, result in results.items()]
            )
            return [
                TextContent(
                    type="text",
                    text=f"Successfully cleaned processes:\n{formatted_results}",
                )
            ]
        except Exception as e:
            return [
                TextContent(type="text", text=f"An error occurred during cleanup: {e}")
            ]

    @mcp.tool()
    @auto_handle_exception
    async def command_ps_detail(
        pid: str = Field(description="The ID of the process to get details for."),
    ) -> Sequence[TextContent]:
        """
        Gets detailed information about a specific background process.
        """
        try:
            p = await process_manager.get_process_info(pid)
            # Format the process info into a markdown string
            duration = "N/A"
            if p.end_time and p.start_time:
                duration_td = p.end_time - p.start_time
                duration = str(duration_td)

            details = (
                f"### Process Details: {p.pid}\n\n"
                f"#### Basic Information\n"
                f"- **Status**: {p.status}\n"
                f"- **Command**: `{' '.join(p.command)}`\n"
                f"- **Description**: {p.description}\n"
                f"- **Labels**: {', '.join(p.labels) if p.labels else 'None'}\n\n"
                f"#### Time Information\n"
                f"- **Start Time**: {p.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"- **End Time**: {p.end_time.strftime('%Y-%m-%d %H:%M:%S') if p.end_time else 'N/A'}\n"
                f"- **Duration**: {duration}\n\n"
                f"#### Execution Information\n"
                f"- **Working Directory**: {p.directory}\n"
                f"- **Exit Code**: {p.exit_code if p.exit_code is not None else 'N/A'}\n"
                f"- **Error Message**: {p.error_message if p.error_message else 'N/A'}\n\n"
                f"#### Output Information\n"
                f"- Use `command_ps_logs` to view process output.\n"
                f'- Example: `command_ps_logs(pid="{p.pid}")`'
            )
            return [TextContent(type="text", text=details)]
        except ProcessNotFoundError:
            return [TextContent(type="text", text=f"Process with ID {pid} not found")]
        except Exception as e:
            return [TextContent(type="text", text=f"An error occurred: {e}")]
