import asyncio
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime
import re
import sys
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from pydantic import Field

from .exceptions import (
    CommandExecutionError,
    CommandTimeoutError,
    ProcessNotFoundError,
    ProcessControlError
)
from .interfaces import ICommandExecutor
from .models import ProcessStatus

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
                        output_key=entry.output_key
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
        mcp: FastMCP, command_executor: ICommandExecutor,
        *,
        allowed_commands: List[str],
        default_encoding: str,
        # TODO: add other options if needed
    ) -> None:
    """
    Defines MCP tools for command execution and process management.

    Args:
        mcp: The MCP server instance.
        command_executor: The command executor instance.
        allowed_commands: A list of commands that are allowed to be executed.
        default_encoding: The default encoding for the command output.
    """

    logger.info(f"Allowed commands: {allowed_commands}")

    @mcp.tool()
    async def command_execute(
        command: str = Field(description="The command to execute. Only allowed commands are: " + ", ".join(allowed_commands)),
        args: Optional[List[str]] = Field(None, description="The arguments for the command."),
        directory: str = Field(description="The working directory for the command."),
        stdin: Optional[str] = Field(None, description="Input to pass to the command via stdin."),
        timeout: int = Field(15, description="Maximum execution time in seconds."),
        envs: Optional[Dict[str, str]] = Field(None, description="Additional environment variables for the command."),
        encoding: str = Field(default_encoding, description="Character encoding for the command output (e.g., 'utf-8', 'gbk', 'cp936')."),
        limit_lines: Optional[int] = Field(500, description="Maximum number of lines to return in each TextContent.")
    ) -> Sequence[TextContent]:
        """
        Executes a single shell command and returns the result.
        This tool only supports simple commands in the form of `command + args` and does not parse complex shell operators.
        """
        # Check if command is allowed
        if command not in allowed_commands:
            return [TextContent(type="text", text=f"Command '{command}' is not allowed. Allowed commands: {', '.join(allowed_commands)}")]
        
        try:
            result = await command_executor.execute_command(
                command=[command] + (args or []),
                directory=directory,
                stdin_data=stdin.encode() if stdin else None,
                timeout=timeout,
                envs=envs,
                encoding=encoding or default_encoding,
                limit_lines=limit_lines or 500,
            )

            # Always return 3 TextContent items as per FDS specification
            return [
                TextContent(type="text", text=f"**exit with {result.exit_code}**"),
                TextContent(type="text", text=f"---\nstdout:\n---\n{result.stdout}\n"),
                TextContent(type="text", text=f"---\nstderr:\n---\n{result.stderr}\n")
            ]
        except CommandTimeoutError as e:
            return [TextContent(type="text", text=f"Command timed out: {e}")]
        except CommandExecutionError as e:
            return [TextContent(type="text", text=f"Command execution failed: {e}")]
        except Exception as e:
            # Catch any unexpected exceptions to prevent MCP protocol stack crashes
            import traceback
            error_details = traceback.format_exc()
            return [TextContent(type="text", text=f"Unexpected error during command execution: {e}\nDetails: {error_details[:500]}...")]

    @mcp.tool()
    async def command_bg_start(
        command: str = Field(description="The command to execute. Only allowed commands are: " + ", ".join(allowed_commands)),
        args: Optional[List[str]] = Field(None, description="The arguments for the command."),
        directory: str = Field(description="The working directory for the command."),
        description: str = Field(description="A description for the command."),
        labels: Optional[List[str]] = Field(None, description="Labels to categorize the command."),
        stdin: Optional[str] = Field(None, description="Input to pass to the command via stdin."),
        envs: Optional[Dict[str, str]] = Field(None, description="Additional environment variables for the command."),
        encoding: str = Field(default_encoding, description="Character encoding for the command output."),
        timeout: int = Field(15, description="Maximum execution time in seconds.")
    ) -> Sequence[TextContent]:
        """
        Starts a background process for a single command and provides fine-grained management.
        """
        # Check if command is allowed
        if command not in allowed_commands:
            return [TextContent(type="text", text=f"Command '{command}' is not allowed. Allowed commands: {', '.join(allowed_commands)}")]
        
        try:
            process = await command_executor.start_background_command(
                command=[command] + (args or []),
                directory=directory,
                description=description,
                stdin_data=stdin.encode() if stdin else None,
                timeout=timeout,
                envs=envs,
                encoding=encoding or default_encoding,
                labels=labels,
            )
            return [TextContent(type="text", text=f"Process started with PID: {process.pid}")]
        except CommandExecutionError as e:
            return [TextContent(type="text", text=f"Failed to start background process: {e}")]

    @mcp.tool()
    async def command_ps_list(
        labels: Optional[List[str]] = Field(None, description="Filter processes by labels."),
        status: Optional[str] = Field(None, description="Filter by status ('running', 'completed', 'failed', 'terminated', 'error').")
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
                        type="text", text=f"Invalid status: {status}. Must be one of {', '.join([s.value for s in ProcessStatus])}"
                    )
                ]

        processes = await command_executor.list_process(
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
            rows.append(f"| {p.pid[:8]} | {p.status.value} | `{command_str}` | {p.description} | {labels_str} |")

        return [TextContent(type="text", text="\n".join(rows))]

    @mcp.tool()
    async def command_ps_stop(
        pid: str = Field(description="The ID of the process to stop."),
        force: Optional[bool] = Field(False, description="Whether to force stop the process (default: false).")
    ) -> Sequence[TextContent]:
        """
        Stops a running process.
        """
        try:
            await command_executor.stop_process(pid, force or False)
            return [TextContent(type="text", text=f"Process {pid} stopped.")]
        except ProcessNotFoundError as e:
            return [TextContent(type="text", text=str(e))]
        except ProcessControlError as e:
            return [
                TextContent(
                    type="text", text=f"Error stopping process {pid}: {e}"
                )
            ]

    @mcp.tool()
    async def command_ps_logs(
        pid: str = Field(description="The ID of the process to get output from."),
        tail: Optional[int] = Field(None, description="Number of lines to show from the end."),
        since: Optional[str] = Field(None, description="Show logs since this timestamp (ISO format, e.g., '2023-05-06T14:30:00')."),
        until: Optional[str] = Field(None, description="Show logs until this timestamp (ISO format, e.g., '2023-05-06T15:30:00')."),
        with_stdout: Optional[bool] = Field(True, description="Show standard output."),
        with_stderr: Optional[bool] = Field(False, description="Show error output."),
        add_time_prefix: Optional[bool] = Field(True, description="Add a timestamp prefix to each output line."),
        time_prefix_format: Optional[str] = Field(None, description="Format of the timestamp prefix, using strftime format."),
        follow_seconds: Optional[int] = Field(1, description="Wait for the specified number of seconds to get new logs. If 0, return immediately."),
        limit_lines: Optional[int] = Field(500, description="Maximum number of lines to return in each TextContent."),
        grep: Optional[str] = Field(None, description="Perl standard regular expression to filter output."),
        grep_mode: Optional[str] = Field("line", description="Regex match mode: 'line' (matching line) or 'content' (matching content itself).")
    ) -> Sequence[TextContent]:
        """
        Gets the output of a process, with support for filtering via regular expressions.
        """
        try:
            since_ts = datetime.fromisoformat(since).timestamp() if since else None
        except (ValueError, TypeError) as e:
            return [TextContent(type="text", text=f"Invalid 'since' timestamp format: {since}. Expected ISO format (e.g., '2023-05-06T14:30:00').")]
        
        try:
            until_ts = datetime.fromisoformat(until).timestamp() if until else None
        except (ValueError, TypeError) as e:
            return [TextContent(type="text", text=f"Invalid 'until' timestamp format: {until}. Expected ISO format (e.g., '2023-05-06T15:30:00').")]

        try:
            # First, get process details for the header
            process_info = await command_executor.get_process_detail(pid)
            
            # Create the process info header
            command_str = " ".join(process_info.command)
            status_description = "进程仍在运行" if process_info.status.value == "running" else f"进程已{process_info.status.value}"
            
            process_header = (
                f"**进程{pid}（状态：{process_info.status.value}）**\n"
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
                    async for log_entry in command_executor.get_process_logs(
                        pid, "stdout", since_ts, until_ts, tail
                    ):
                        stdout_logs.append(log_entry)
                    
                    if stdout_logs:
                        # Apply grep filtering if specified
                        filtered_logs = await _apply_grep_filter(stdout_logs, grep, grep_mode)
                        
                        # Format logs with time prefix if requested
                        formatted_logs = []
                        for log in filtered_logs[:limit_lines or 500]:
                            if add_time_prefix:
                                time_str = log.timestamp.strftime(time_format)
                                formatted_logs.append(f"[{time_str}] {log.text}")
                            else:
                                formatted_logs.append(log.text)
                        
                        if formatted_logs:
                            stdout_content = (
                                f"---\nstdout: 匹配内容（根据grep_mode）\n---\n" +
                                "\n".join(formatted_logs)
                            )
                            result_contents.append(TextContent(type="text", text=stdout_content))
                except Exception:
                    # If there's an error getting stdout, continue to stderr
                    pass
            
            # Process stderr if requested
            if with_stderr:
                try:
                    stderr_logs = []
                    async for log_entry in command_executor.get_process_logs(
                        pid, "stderr", since_ts, until_ts, tail
                    ):
                        stderr_logs.append(log_entry)
                    
                    if stderr_logs:
                        # Apply grep filtering if specified
                        filtered_logs = await _apply_grep_filter(stderr_logs, grep, grep_mode)
                        
                        # Format logs with time prefix if requested
                        formatted_logs = []
                        for log in filtered_logs[:limit_lines or 500]:
                            if add_time_prefix:
                                time_str = log.timestamp.strftime(time_format)
                                formatted_logs.append(f"[{time_str}] {log.text}")
                            else:
                                formatted_logs.append(log.text)
                        
                        if formatted_logs:
                            stderr_content = (
                                f"---\nstderr: 匹配内容（根据grep_mode）\n---\n" +
                                "\n".join(formatted_logs)
                            )
                            result_contents.append(TextContent(type="text", text=stderr_content))
                except Exception:
                    # If there's an error getting stderr, continue
                    pass
            
            # Handle follow_seconds - simulate waiting for new logs
            if follow_seconds and follow_seconds > 0:
                await asyncio.sleep(min(follow_seconds, 5))  # Cap at 5 seconds for safety
            
            # If no stdout/stderr was requested or found, add appropriate message
            if not with_stdout and not with_stderr:
                return [TextContent(type="text", text="No logs requested.")]
            elif len(result_contents) == 1:  # Only header, no logs found
                return [TextContent(type="text", text="No logs found.")]
            
            return result_contents
            
        except ProcessNotFoundError as e:
            return [TextContent(type="text", text=str(e))]
        except Exception as e:
            return [TextContent(type="text", text=f"An error occurred: {e}")]

    @mcp.tool()
    async def command_ps_clean(
        pids: List[str] = Field(description="A list of process IDs to clean.")
    ) -> Sequence[TextContent]:
        """
        Cleans up completed or failed processes.
        """
        if not pids:
            return [TextContent(type="text", text="No process IDs provided.")]
        try:
            results = await command_executor.clean_process(pids)
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
            return [TextContent(type="text", text=f"An error occurred during cleanup: {e}")]

    @mcp.tool()
    async def command_ps_detail(
        pid: str = Field(description="The ID of the process to get details for.")
    ) -> Sequence[TextContent]:
        """
        Gets detailed information about a specific process.
        """
        try:
            p = await command_executor.get_process_detail(pid)
            # Format the process info into a markdown string
            duration = "N/A"
            if p.end_time and p.start_time:
                duration_td = p.end_time - p.start_time
                duration = str(duration_td)

            details = (
                f"### Process Details: {p.pid}\n\n"
                f"#### Basic Information\n"
                f"- **Status**: {p.status.value}\n"
                f"- **Command**: `{' '.join(p.command)}`\n"
                f"- **Description**: {p.description}\n"
                f"- **Labels**: {', '.join(p.labels) if p.labels else 'None'}\n\n"
                f"#### Time Information\n"
                f"- **Start Time**: {p.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"- **End Time**: {p.end_time.strftime('%Y-%m-%d %H:%M:%S') if p.end_time else 'N/A'}\n"
                f"- **Duration**: {duration}\n\n"
                f"#### Execution Information\n"
                f"- **Working Directory**: {p.directory}\n"
                f"- **Exit Code**: {p.exit_code if p.exit_code is not None else 'N/A'}\n\n"
                f"#### Output Information\n"
                f"- Use `command_ps_logs` to view process output.\n"
                f"- Example: `command_ps_logs(pid=\"{p.pid}\")`"
            )
            return [TextContent(type="text", text=details)]
        except ProcessNotFoundError as e:
            return [TextContent(type="text", text=str(e))]
        except Exception as e:
            return [TextContent(type="text", text=f"An error occurred: {e}")]
