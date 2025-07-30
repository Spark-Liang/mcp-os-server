from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import AsyncGenerator, Dict, Optional

from .exceptions import (
    OutputClearError,
    OutputRetrievalError,
    ProcessNotFoundError,
    StorageError,
)
from .interfaces import IOutputManager, OutputMessageEntry
from .output_logger import SqliteOutputLogger

_logger = logging.getLogger(__name__)


class OutputManager(IOutputManager):
    """
    A concrete implementation of IOutputManager that uses YamlOutputLogger
    to store and manage output for multiple processes.
    """

    def __init__(self, output_storage_path: str = "./command_outputs"):
        self.output_storage_path = output_storage_path
        os.makedirs(self.output_storage_path, exist_ok=True)
        self._loggers: Dict[str, Dict[str, SqliteOutputLogger]] = {}
        self._shutdown_flag = False

    async def initialize(self) -> None:
        """Initializes the OutputManager. No-op for this implementation as setup is done in __init__."""
        pass

    def _get_log_file_path(self, process_id: str, output_key: str) -> str:
        process_output_dir = os.path.join(self.output_storage_path, process_id)
        os.makedirs(process_output_dir, exist_ok=True)
        return os.path.join(process_output_dir, "process_output.db")

    def _get_logger_for_process(
        self, process_id: str, output_key: str
    ) -> SqliteOutputLogger:
        if self._shutdown_flag:
            raise Exception("OutputManager is shutting down or already shut down.")

        if process_id not in self._loggers:
            process_dir = os.path.join(self.output_storage_path, process_id)
            os.makedirs(process_dir, exist_ok=True)
            self._loggers[process_id] = {}

        if output_key not in self._loggers[process_id]:
            log_file_path = self._get_log_file_path(process_id, output_key)
            self._loggers[process_id][output_key] = SqliteOutputLogger(
                log_file_path, sub_id=output_key
            )
        return self._loggers[process_id][output_key]

    async def store_output(
        self,
        process_id: str,
        output_key: str,
        message: str | list[str],
        timestamp: Optional[datetime] = None,
    ) -> None:
        if not process_id or not output_key:
            raise ValueError("Process ID and output key cannot be empty.")
        try:
            logger = self._get_logger_for_process(process_id, output_key)
            if isinstance(message, list):
                logger.add_messages(message, timestamp=timestamp)
            else:
                logger.add_message(message, timestamp=timestamp)
        except Exception as e:
            raise StorageError(
                f"Failed to store output for process {process_id}: {e}"
            ) from e

    async def get_output(
        self,
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
        grep_pattern: Optional[str] = None,
        grep_mode: str = "line",
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        if not process_id or not output_key:
            raise ValueError("Process ID and output key cannot be empty.")

        if process_id not in self._loggers:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")

        if output_key not in self._loggers[process_id]:
            return

        try:
            logger = self._loggers[process_id][output_key]
            async for entry in logger.get_logs(
                tail=tail,
                since=datetime.fromtimestamp(since) if since else None,
                until=datetime.fromtimestamp(until) if until else None,
            ):
                if grep_pattern:
                    if grep_mode == "line":
                        if re.search(grep_pattern, entry.text):
                            yield OutputMessageEntry(
                                timestamp=entry.timestamp,
                                text=entry.text,
                                output_key=output_key,
                            )
                    elif grep_mode == "content":
                        match = re.search(grep_pattern, entry.text)
                        if match:
                            yield OutputMessageEntry(
                                timestamp=entry.timestamp,
                                text=match.group(0),
                                output_key=output_key,
                            )
                    else:
                        raise ValueError("grep_mode must be 'line' or 'content'.")
                else:
                    yield OutputMessageEntry(
                        timestamp=entry.timestamp,
                        text=entry.text,
                        output_key=output_key,
                    )
        except Exception as e:
            raise OutputRetrievalError(
                f"Failed to retrieve output for process {process_id}: {e}"
            ) from e

    async def clear_output(self, process_id: str) -> None:
        if not process_id:
            raise ValueError("Process ID cannot be empty.")

        if process_id not in self._loggers:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")

        try:
            for output_key in list(self._loggers[process_id].keys()):
                logger = self._loggers[process_id][output_key]
                logger.close()
                del self._loggers[process_id][output_key]

            log_file_path = self._get_log_file_path(process_id, "any_output_key")
            if os.path.exists(log_file_path):
                os.remove(log_file_path)
            del self._loggers[process_id]

            process_dir = os.path.join(self.output_storage_path, process_id)
            if os.path.exists(process_dir) and not os.listdir(process_dir):
                os.rmdir(process_dir)
        except Exception as e:
            raise OutputClearError(
                f"Failed to clear output for process {process_id}: {e}"
            ) from e

    async def shutdown(self) -> None:
        self._shutdown_flag = True
        last_exception = None
        for process_id in list(self._loggers.keys()):
            for output_key in list(self._loggers[process_id].keys()):
                try:
                    logger = self._loggers[process_id][output_key]
                    logger.close()
                    del self._loggers[process_id][output_key]
                except Exception as e:
                    _logger.error(
                        "Error during shutdown for process %s, output %s: %s",
                        process_id,
                        output_key,
                        e,
                    )
                    last_exception = e

            log_file_path = self._get_log_file_path(process_id, "any_output_key")
            if os.path.exists(log_file_path):
                os.remove(log_file_path)

            process_dir = os.path.join(self.output_storage_path, process_id)
            if os.path.exists(process_dir) and not os.listdir(process_dir):
                os.rmdir(process_dir)
            if process_id in self._loggers:
                del self._loggers[process_id]
        self._loggers.clear()
        if os.path.exists(self.output_storage_path) and not os.listdir(
            self.output_storage_path
        ):
            try:
                os.rmdir(self.output_storage_path)
            except OSError as e:
                _logger.warning(
                    "Could not remove output storage directory %s: %s",
                    self.output_storage_path,
                    e,
                )
        if last_exception:
            raise last_exception
