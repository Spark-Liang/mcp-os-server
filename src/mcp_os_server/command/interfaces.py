"""
This module defines the interfaces (Protocols) for the core components
of the MCP Command Server, establishing the contracts between different parts
of the application.
"""

from __future__ import annotations

import sys
from abc import abstractmethod
from datetime import datetime
from pathlib import Path
from typing import (
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from .models import (
    MessageEntry,
    OutputMessageEntry,
    ProcessInfo,
    ProcessStatus,
)


@runtime_checkable
class IOutputManager(Protocol):
    """
    Interface for a process output manager.
    It is responsible for independently storing, retrieving, and managing the output data
    of all processes. It should not depend on ProcessManager or any higher-level components.
    """

    async def store_output(
        self, process_id: str, output_key: str, message: str | list[str]
    ) -> None:
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

    async def get_output(
        self,
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
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
    def pid(self) -> str: ...

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

    async def get_output(
        self,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
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

    async def clean(self) -> Optional[str]:
        """
        Cleans up all related resources and output of this process.

        Returns:
            Optional[str]: A error message about the cleanup, or None if the cleanup is successful.

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

    async def start_process(
        self,
        command: List[str],
        directory: str,
        description: str,
        timeout: int,
        stdin_data: Optional[bytes | str] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: str = sys.getdefaultencoding(),
        labels: Optional[List[str]] = None,
        extra_paths: Optional[List[str | Path]] = None,
    ) -> IProcess:
        """
        Starts a new background process.

        Args:
            command (List[str]): The command and its arguments to execute.
            directory (str): The working directory for the command execution.
            description (str): A description of the process.
            stdin_data (Optional[bytes | str]): Input byte data to pass to the command via stdin.
                If stdin_data is a string, it will be encoded to bytes using the encoding parameter.
                If stdin_data is bytes, it will be passed to the command via stdin.
            timeout (int): Maximum execution time in seconds.
            envs (Optional[Dict[str, str]]): Additional environment variables for the command.
            encoding (Optional[str]): Character encoding for the command's output.
            labels (Optional[List[str]]): A list of labels for process classification.
            extra_paths (Optional[List[str | Path]]): A list of extra paths to be added to the PATH environment variable.

        Returns:
            IProcess: An instance of the started process.

        Raises:
            ValueError: If command or directory is invalid.
            CommandExecutionError: If the command cannot be started or fails to execute or failed to write stdin_data to subprocess.
            PermissionError: If there are insufficient permissions to execute the command.
        """
        ...

    async def stop_process(
        self, process_id: str, force: bool = False, reason: Optional[str] = None
    ) -> None:
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

    async def list_processes(
        self, status: Optional[ProcessStatus] = None, labels: Optional[List[str]] = None
    ) -> List[ProcessInfo]:
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
class IWebManager(Protocol):
    """
    Interface for the Web Manager.
    Responsible for providing a web-based management interface for monitoring and managing
    background processes. It relies on IProcessManager to get process information and mounts
    its web interface onto the HTTP service provided by the MCP Server.
    """

    async def initialize(self, process_manager: IProcessManager) -> None:
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
        url_prefix: str = "",
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

    async def shutdown(self) -> None:
        """
        Shuts down the Web Manager and releases all resources.

        Raises:
            Exception: For any unexpected errors during shutdown.
        """
        ...
