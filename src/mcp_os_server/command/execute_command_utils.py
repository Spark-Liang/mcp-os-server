from typing import (
    Dict,
    List,
    Optional,
)

import anyio

from .interfaces import IProcessManager
from .models import CommandResult


async def execute_command(
    process_manager: IProcessManager,
    command: List[str],
    directory: str,
    encoding: str,
    stdin_data: Optional[bytes | str] = None,
    timeout: Optional[int] = None,
    envs: Optional[Dict[str, str]] = None,
    limit_lines: Optional[int] = None,
    labels: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> CommandResult:
    """
    Synchronously executes a single command and waits for it to complete.
    Args:
        command (List[str]): The command and its arguments to execute.
        directory (str): The working directory for the command execution.
        encoding (str): Character encoding for the stdin_data and command's output.
        stdin_data (Optional[bytes | str]): Input byte data to pass to the command via stdin.
            If stdin_data is a string, it will be encoded to bytes using the encoding parameter.
            If stdin_data is bytes, it will be passed to the command via stdin.
        timeout (Optional[int]): Maximum execution time in seconds.
        envs (Optional[Dict[str, str]]): Additional environment variables for the command.
        limit_lines (Optional[int]): The maximum number of lines to return per TextContent.
        labels (Optional[List[str]]): A list of labels for process classification.
        description (Optional[str]): A description of the process.
    Returns:
        CommandResult: The execution result of the command, including stdout, stderr,
                        exit code, and execution time.

    Raises:
        ValueError: If command or directory is invalid.
        CommandExecutionError: If the command cannot be started or fails to execute.
        CommandTimeoutError: If the command execution times out.
        PermissionError: If there are insufficient permissions to execute the command.
    """
    start_time = anyio.current_time()
    if description is None:
        description = f"Execute: {' '.join(command)}"
    process = await process_manager.start_process(
        command=command,
        directory=directory,
        description=description,
        stdin_data=stdin_data,
        timeout=timeout,
        envs=envs,
        encoding=encoding,
        labels=labels,
    )
    info = await process.wait_for_completion(timeout=timeout)
    end_time = anyio.current_time()
    stdout_lines = [entry.text async for entry in process.get_output("stdout")]
    stderr_lines = [entry.text async for entry in process.get_output("stderr")]
    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)
    return CommandResult(
        stdout=stdout,
        stderr=stderr,
        exit_status=info.status,
        exit_code=info.exit_code if info.exit_code is not None else -1,
        execution_time=end_time - start_time,
    )
