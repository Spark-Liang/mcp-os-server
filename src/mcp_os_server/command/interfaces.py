"""
This module defines the interfaces (Protocols) for the core components
of the MCP Command Server, establishing the contracts between different parts
of the application.
"""
from __future__ import annotations
import asyncio
from typing import Dict, List, Optional, Tuple, AsyncGenerator, Protocol, Union, runtime_checkable
from pydantic import BaseModel, Field
import enum
from datetime import datetime
from abc import abstractmethod

from .models import (
    OutputMessageEntry,
    MessageEntry,
    ProcessStatus,
    ProcessInfo,
    CommandResult,
)


@runtime_checkable
class IOutputManager(Protocol):
    """
    Interface for a process output manager.
    It is responsible for independently storing, retrieving, and managing the output data
    of all processes. It should not depend on ProcessManager or any higher-level components.
    """

    async def store_output(self,
                           process_id: str,
                           output_key: str,
                           message: str | list[str]) -> None:
        """
        Stores the specified output data (stdout or stderr) for a given process.

        Args:
            process_id (str): The unique identifier of the process.
            output_key (str): The key for the output content, e.g., "stdout" or "stderr".
            message (str | list[str]): The output message or list of messages.

        Raises:
            ValueError: If process_id or output_key is invalid.
            StorageError: If an error occurs during storage.
        """
        ...

    async def get_output(self,
                         process_id: str,
                         output_key: str,
                         since: Optional[float] = None,
                         until: Optional[float] = None,
                         tail: Optional[int] = None) -> AsyncGenerator[OutputMessageEntry, None]:
        """
        Asynchronously retrieves the specified output stream (stdout or stderr) for a given process.

        Args:
            process_id (str): The unique identifier of the process.
            output_key (str): The key for the output content, e.g., "stdout" or "stderr".
            since (Optional[float]): Timestamp to return logs after.
            until (Optional[float]): Timestamp to return logs before.
            tail (Optional[int]): The number of lines to return from the end.

        Yields:
            OutputMessageEntry: A log entry containing timestamp, content, and output key.

        Raises:
            ValueError: If process_id or output_key is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            OutputRetrievalError: If an error occurs while retrieving the output.
        """
        ...

    async def clear_output(self, process_id: str) -> None:
        """
        Clears all stored output (both stdout and stderr) for a specified process.

        Args:
            process_id (str): The unique identifier of the process.

        Raises:
            ValueError: If process_id is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            OutputClearError: If an error occurs while clearing the output.
        """
        ...

    async def shutdown(self) -> None:
        """
        Shuts down the output manager and releases all resources.

        Raises:
            Exception: For any unexpected errors during shutdown.
        """
        ...


class IOutputLogger(Protocol):
    """Interface for an output logger, defining log read and write operations."""

    @abstractmethod
    def add_message(self, message: str) -> None:
        """
        Adds a single message log.

        Args:
            message: The log content.
        """
        ...

    @abstractmethod
    def add_messages(self, messages: List[str]) -> None:
        """
        Adds multiple message logs in a batch.

        Args:
            messages: A list of log content.
        """
        ...

    @abstractmethod
    async def get_logs(
        self,
        tail: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> AsyncGenerator[MessageEntry, None]:
        """
        Retrieves logs that meet the specified criteria.

        Args:
            tail: Return only the last n lines.
            since: Return logs after this specific time.
            until: Return logs before this specific time.

        Yields:
            MessageEntry: A log record object with timestamp and text fields.

        Raises:
            Exception: For any errors that occur during log retrieval.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Closes the log and cleans up resources."""
        ...


@runtime_checkable
class IProcess(Protocol):
    """
    Interface for a single process instance.
    """
    @property
    def pid(self) -> str:
        ...

    async def get_details(self) -> ProcessInfo:
        """
        Gets the detailed information of this process.

        Returns:
            ProcessInfo: The detailed information object of the process.

        Raises:
            ProcessNotFoundError: If the process no longer exists.
            ProcessInfoRetrievalError: If an error occurs while retrieving process info.
        """
        ...

    async def wait_for_completion(self, timeout: Optional[int] = None) -> ProcessInfo:
        """
        Waits for the process to complete.

        Args:
            timeout (Optional[int]): The maximum time to wait in seconds. If None, waits indefinitely.

        Returns:
            ProcessInfo: Detailed information of the process after completion, including exit code.

        Raises:
            ProcessTimeoutError: If the process does not complete within the specified time.
            ProcessControlError: If an error occurs during waiting.
        """
        ...

    async def get_output(self,
                         output_key: str,
                         since: Optional[float] = None,
                         until: Optional[float] = None,
                         tail: Optional[int] = None) -> AsyncGenerator[OutputMessageEntry, None]:
        """
        Asynchronously retrieves the specified output stream of this process.

        Args:
            output_key (str): The key for the output content, e.g., "stdout" or "stderr".
            since (Optional[float]): Timestamp to return logs after.
            until (Optional[float]): Timestamp to return logs before.
            tail (Optional[int]): The number of lines to return from the end.

        Yields:
            OutputMessageEntry: A log entry containing timestamp, content, and output key.

        Raises:
            ValueError: If output_key is empty.
            OutputRetrievalError: If an error occurs while retrieving the output.
        """
        ...

    async def stop(self, force: bool = False, reason: Optional[str] = None) -> None:
        """
        Stops this process.

        Args:
            force (bool): Whether to forcefully stop the process (e.g., via SIGKILL).
            reason (Optional[str]): The reason for actively terminating the process.

        Raises:
            ProcessControlError: If an error occurs while stopping the process.
        """
        ...

    async def clean(self) -> str:
        """
        Cleans up all related resources and output of this process.

        Returns:
            str: A message about the result of the cleanup.

        Raises:
            ProcessCleanError: If an error occurs during cleanup.
        """
        ...


@runtime_checkable
class IProcessManager(Protocol):
    """
    Interface for the Process Manager.
    Responsible for low-level process management capabilities, including process startup,
    monitoring, stopping, termination, status query, and cleanup, as well as timeout control.
    It relies on IOutputManager to handle process output storage.
    """

    async def initialize(self) -> None:
        """
        Initializes the process manager.
        This method is for performing any necessary setup or resource allocation.

        Raises:
            IOError: If an IO error occurs during initialization.
            Exception: For any other unexpected errors during initialization.
        """
        ...

    async def start_process(self,
                            command: List[str],
                            directory: str,
                            description: str,
                            stdin_data: Optional[bytes | str] = None,
                            timeout: Optional[int] = None,
                            envs: Optional[Dict[str, str]] = None,
                            encoding: Optional[str] = None,
                            labels: Optional[List[str]] = None) -> IProcess:
        """
        Starts a new background process.

        Args:
            command (List[str]): The command and its arguments to execute.
            directory (str): The working directory for the command execution.
            description (str): A description of the process.
            stdin_data (Optional[bytes | str]): Input byte data to pass to the command via stdin. 
                If stdin_data is a string, it will be encoded to bytes using the encoding parameter.
                If stdin_data is bytes, it will be passed to the command via stdin.
            timeout (Optional[int]): Maximum execution time in seconds.
            envs (Optional[Dict[str, str]]): Additional environment variables for the command.
            encoding (Optional[str]): Character encoding for the command's output.
            labels (Optional[List[str]]): A list of labels for process classification.

        Returns:
            IProcess: An instance of the started process.

        Raises:
            ValueError: If command or directory is invalid.
            CommandExecutionError: If the command cannot be started or fails to execute.
            PermissionError: If there are insufficient permissions to execute the command.
        """
        ...

    async def stop_process(self, process_id: str, force: bool = False, reason: Optional[str] = None) -> None:
        """
        Stops a running background process.

        Args:
            process_id (str): The unique identifier of the process to stop.
            force (bool): Whether to forcefully stop the process (e.g., via SIGKILL).
            reason (Optional[str]): The reason for actively terminating the process.

        Raises:
            ValueError: If process_id is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            ProcessControlError: If an error occurs while stopping the process.
        """
        ...

    async def get_process_info(self, process_id: str) -> ProcessInfo:
        """
        Gets detailed information about a specific background process.

        Args:
            process_id (str): The unique identifier of the process to get info for.

        Returns:
            ProcessInfo: A detailed information object of the process.

        Raises:
            ValueError: If process_id is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            ProcessInfoRetrievalError: If an error occurs while retrieving process info.
        """
        ...

    async def list_processes(self,
                             status: Optional[ProcessStatus] = None,
                             labels: Optional[List[str]] = None) -> List[ProcessInfo]:
        """
        Lists running or completed background processes.

        Args:
            status (Optional[ProcessStatus]): Filter processes by status.
            labels (Optional[List[str]]): Filter processes by labels.

        Returns:
            List[ProcessInfo]: A list of process information matching the criteria.

        Raises:
            ProcessListRetrievalError: If an error occurs while retrieving the process list.
        """
        ...

    async def clean_processes(self, process_ids: List[str]) -> Dict[str, Optional[str]]:
        """
        Cleans up completed or failed background processes.

        Args:
            process_ids (List[str]): A list of process IDs to clean up.

        Returns:
            Dict[str, str]: A dictionary with process IDs as keys and cleanup results as values.

        Raises:
            ValueError: If process_ids is an empty list.
            ProcessCleanError: If a general error occurs during cleanup.
        """
        ...

    async def shutdown(self) -> None:
        """
        Shuts down the process manager, stopping all running processes and releasing resources.

        Raises:
            Exception: For any unexpected errors during shutdown.
        """
        ...

    async def get_process(self, process_id: str) -> IProcess:
        """
        Gets a specific background process instance.

        Args:
            process_id (str): The unique identifier of the process to get.

        Returns:
            IProcess: An instance of the process.

        Raises:
            ValueError: If process_id is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
        """
        ...


@runtime_checkable
class ICommandExecutor(Protocol):
    """
    Interface for the Shell Executor.
    Coordinates IProcessManager to execute commands and handles their I/O streams.
    It provides capabilities for synchronous command execution and for starting/managing
    background processes.
    """

    async def initialize(self) -> None:
        """
        Initializes the Shell Executor.
        This method is for performing any necessary setup or resource allocation.

        Raises:
            IOError: If an IO error occurs during initialization.
            Exception: For any other unexpected errors during initialization.
        """
        ...

    async def execute_command(
        self,
        command: List[str],
        directory: str,
        stdin_data: Optional[bytes | str] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        limit_lines: Optional[int] = None
    ) -> CommandResult:
        """
        Synchronously executes a single command and waits for it to complete.

        Args:
            command (List[str]): The command and its arguments to execute.
            directory (str): The working directory for the command execution.
            stdin_data (Optional[bytes | str]): Input byte data to pass to the command via stdin.
                If stdin_data is a string, it will be encoded to bytes using the encoding parameter.
                If stdin_data is bytes, it will be passed to the command via stdin.
            timeout (Optional[int]): Maximum execution time in seconds.
            envs (Optional[Dict[str, str]]): Additional environment variables for the command.
            encoding (Optional[str]): Character encoding for the command's output.
            limit_lines (Optional[int]): The maximum number of lines to return per TextContent.

        Returns:
            CommandResult: The execution result of the command, including stdout, stderr,
                           exit code, and execution time.

        Raises:
            ValueError: If command or directory is invalid.
            CommandExecutionError: If the command cannot be started or fails to execute.
            CommandTimeoutError: If the command execution times out.
            PermissionError: If there are insufficient permissions to execute the command.
        """
        ...

    async def start_background_command(
        self,
        command: List[str],
        directory: str,
        description: str,
        stdin_data: Optional[bytes | str] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        labels: Optional[List[str]] = None
    ) -> IProcess:
        """
        Starts a background command.

        Args:
            command (List[str]): The command and its arguments to execute.
            directory (str): The working directory for the command execution.
            description (str): A description of the process.
            stdin_data (Optional[bytes | str]): Input byte data to pass to the command via stdin.
                If stdin_data is a string, it will be encoded to bytes using the encoding parameter.
                If stdin_data is bytes, it will be passed to the command via stdin.
            timeout (Optional[int]): Maximum execution time in seconds.
            envs (Optional[Dict[str, str]]): Additional environment variables for the command.
            encoding (Optional[str]): Character encoding for the command's output.
            labels (Optional[List[str]]): A list of labels for process classification.

        Returns:
            IProcess: An instance of the started background process.

        Raises:
            ValueError: If command or directory is invalid.
            CommandExecutionError: If the command cannot be started or fails to execute.
            PermissionError: If there are insufficient permissions to execute the command.
        """
        ...

    async def get_process_logs(
        self,
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        """
        Retrieves the output stream of a background command.

        Args:
            process_id (str): The unique identifier of the background process.
            output_key (str): The key for the output content, e.g., "stdout" or "stderr".
            since (Optional[float]): Timestamp to return logs after.
            until (Optional[float]): Timestamp to return logs before.
            tail (Optional[int]): The number of lines to return from the end.

        Yields:
            OutputMessageEntry: A log entry containing timestamp, content, and output key.

        Raises:
            ValueError: If process_id or output_key is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            OutputRetrievalError: If an error occurs while retrieving the output.
        """
        ...

    async def stop_process(
        self, 
        process_id: str, 
        force: bool = False, 
        reason: Optional[str] = None
    ) -> None:
        """
        Stops a background command.

        Args:
            process_id (str): The unique identifier of the background process to stop.
            force (bool): Whether to forcefully stop the process.
            reason (Optional[str]): The reason for actively terminating the process.

        Raises:
            ValueError: If process_id is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            ProcessControlError: If an error occurs while stopping the process.
        """
        ...

    async def list_process(
            self,
            status: Optional[ProcessStatus] = None,
            labels: Optional[List[str]] = None,
            limit: Optional[int] = None
        ) -> List[ProcessInfo]:
        """
        Lists background commands.

        Args:
            status (Optional[ProcessStatus]): Filter commands by status.
            labels (Optional[List[str]]): Filter commands by labels.

        Returns:
            List[ProcessInfo]: A list of background command information matching the criteria.

        Raises:
            ProcessListRetrievalError: If an error occurs while retrieving the process list.
        """
        ...

    async def get_process_detail(self, process_id: str) -> ProcessInfo:
        """
        Gets detailed information about a background command.

        Args:
            process_id (str): The unique identifier of the background process to get details for.

        Returns:
            ProcessInfo: A detailed information object of the background process.

        Raises:
            ValueError: If process_id is empty.
            ProcessNotFoundError: If the specified process ID does not exist.
            ProcessInfoRetrievalError: If an error occurs while retrieving process info.
        """
        ...

    async def clean_process(self, process_ids: List[str]) -> Dict[str, str]:
        """
        Cleans up completed or failed background commands.

        Args:
            process_ids (List[str]): A list of background process IDs to clean up.

        Returns:
            Dict[str, str]: A dictionary with process IDs as keys and cleanup results as values.

        Raises:
            ValueError: If process_ids is an empty list.
            ProcessCleanError: If a general error occurs during cleanup.
        """
        ...

    async def shutdown(self) -> None:
        """
        Shuts down the Shell Executor, stopping all related background processes and releasing resources.

        Raises:
            Exception: For any unexpected errors during shutdown.
        """
        ...

    async def get_process_manager(self) -> IProcessManager:
        """
        Gets the process manager instance.

        Returns:
            IProcessManager: The process manager instance.
        """
        ...


@runtime_checkable
class IWebManager(Protocol):
    """
    Interface for the Web Manager.
    Responsible for providing a web-based management interface for monitoring and managing
    background processes. It relies on ICommandExecutor to get process information and mounts
    its web interface onto the HTTP service provided by the MCP Server.
    """

    async def initialize(self, command_executor: ICommandExecutor) -> None:
        """
        Initializes the Web Manager.
        This method is for performing any necessary setup or resource allocation.

        Raises:
            IOError: If an IO error occurs during initialization.
            Exception: For any other unexpected errors during initialization.
        """
        ...

    async def start_web_interface(
            self,
            host: str = "0.0.0.0",
            port: Optional[int] = None,
            debug: bool = False,
            url_prefix: str = ""
        ) -> None:
        """
        Starts the web interface.

        Args:
            host (str): The host address to listen on.
            port (Optional[int]): The port to listen on. If None, a random port is used.
            debug (bool): Whether to enable debug mode.
            url_prefix (str): URL prefix for running the application under a subpath.

        Raises:
            WebInterfaceError: If the web interface fails to start.
            ValueError: If the parameters are invalid.
        """
        ...

    async def get_processes(self,
                            labels: Optional[List[str]] = None,
                            status: Optional[ProcessStatus] = None) -> List[ProcessInfo]:
        """
        Gets information for all processes.

        Args:
            labels (Optional[List[str]]): Filter processes by labels.
            status (Optional[ProcessStatus]): Filter processes by status.

        Returns:
            List[ProcessInfo]: A list of process information matching the criteria.

        Raises:
            WebInterfaceError: If getting the process list fails.
        """
        ...

    async def get_process_detail(self, pid: str) -> ProcessInfo:
        """
        Gets detailed information for a single process.

        Args:
            pid (str): The unique identifier of the process.

        Returns:
            ProcessInfo: A detailed information object of the process.

        Raises:
            ProcessNotFoundError: If the specified process is not found.
            WebInterfaceError: If getting process information fails.
        """
        ...

    async def get_process_output(self,
                                 pid: str,
                                 tail: Optional[int] = None,
                                 since: Optional[datetime] = None,
                                 until: Optional[datetime] = None,
                                 with_stdout: bool = True,
                                 with_stderr: bool = False) -> Dict[str, List[Dict]]:
        """
        Gets process output.

        Args:
            pid (str): The unique identifier of the process.
            tail (Optional[int]): The number of lines to show from the end.
            since (Optional[datetime]): Show logs since this timestamp.
            until (Optional[datetime]): Show logs until this timestamp.
            with_stdout (bool): Whether to show standard output.
            with_stderr (bool): Whether to show standard error.

        Returns:
            Dict[str, List[Dict]]: A dictionary containing stdout and stderr, each being a list of log entries.

        Raises:
            ProcessNotFoundError: If the specified process is not found.
            OutputRetrievalError: If retrieving output fails.
            WebInterfaceError: If a web interface operation fails.
        """
        ...

    async def stop_process(self, pid: str, force: bool = False) -> Dict[str, str]:
        """
        Stops the specified process.

        Args:
            pid (str): The ID of the process to stop.
            force (bool): Whether to forcefully stop the process.

        Returns:
            Dict[str, str]: Information about the result of the stop operation.

        Raises:
            ProcessNotFoundError: If the specified process is not found.
            ProcessControlError: If stopping the process fails.
            WebInterfaceError: If a web interface operation fails.
        """
        ...

    async def clean_process(self, pid: str) -> Dict[str, str]:
        """
        Cleans the specified process.

        Args:
            pid (str): The ID of the process to clean.

        Returns:
            Dict[str, str]: Information about the result of the clean operation.

        Raises:
            ProcessNotFoundError: If the specified process is not found.
            ProcessCleanError: If cleaning the process fails.
            WebInterfaceError: If a web interface operation fails.
        """
        ...

    async def clean_all_processes(self) -> Dict[str, Union[str, int]]:
        """
        Cleans all completed or failed processes.

        Returns:
            Dict[str, Union[str, int]]: Information about the result of the clean operation,
                                     including the number of cleaned processes.

        Raises:
            ProcessCleanError: If cleaning processes fails.
            WebInterfaceError: If a web interface operation fails.
        """
        ...

    async def clean_selected_processes(self, pids: List[str]) -> Dict[str, List[Dict]]:
        """
        Cleans selected processes.

        Args:
            pids (List[str]): A list of process IDs to clean.

        Returns:
            Dict[str, List[Dict]]: Cleanup results for successful, failed, running, and not found processes.

        Raises:
            ValueError: If the list of process IDs is empty.
            ProcessCleanError: If cleaning processes fails.
            WebInterfaceError: If a web interface operation fails.
        """
        ...

    async def shutdown(self) -> None:
        """
        Shuts down the Web Manager and releases all resources.

        Raises:
            Exception: For any unexpected errors during shutdown.
        """
        ... 