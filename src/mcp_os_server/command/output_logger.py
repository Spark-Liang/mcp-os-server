import asyncio
from datetime import datetime, timezone
import os
import re
from typing import AsyncGenerator, Dict, List, Optional, Union
import yaml

from .interfaces import IOutputLogger, IOutputManager
from .models import MessageEntry, OutputMessageEntry
from .exceptions import ProcessNotFoundError, OutputRetrievalError, OutputClearError, StorageError


class YamlOutputLogger(IOutputLogger):
    """
    An implementation of IOutputLogger that stores logs in a YAML file.
    Each entry is a separate YAML document. This implementation opens and
    closes the file for each operation to ensure simplicity and reliability.
    """

    def __init__(self, log_file_path: str):
        self.log_file_path = log_file_path
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        self._ensure_log_file_exists()

    def _ensure_log_file_exists(self):
        if not os.path.exists(self.log_file_path):
            with open(self.log_file_path, 'w', encoding='utf-8') as f:
                yaml.dump([], f)

    def add_message(self, message: str, timestamp: Optional[datetime] = None) -> None:
        self.add_messages([message], timestamp)

    def add_messages(self, messages: List[str], timestamp: Optional[datetime] = None) -> None:
        current_data = self._read_logs()
        for message in messages:
            entry = MessageEntry(
                timestamp=timestamp if timestamp else datetime.now(),
                text=message
            )
            current_data.append(entry.model_dump(mode='json'))
        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            yaml.dump(current_data, f, sort_keys=False, default_flow_style=False, default_style='|', allow_unicode=True)

    async def get_logs(
        self,
        tail: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> AsyncGenerator[MessageEntry, None]:
        all_logs = self._read_logs()
        filtered_logs = []

        for entry_data in all_logs:
            entry = MessageEntry(**entry_data)
            if since and entry.timestamp < since:
                continue
            if until and entry.timestamp > until:
                continue
            filtered_logs.append(entry)

        if tail is not None:
            filtered_logs = filtered_logs[-tail:]

        for entry in filtered_logs:
            yield entry

    def _read_logs(self) -> List[dict]:
        if not os.path.exists(self.log_file_path):
            return []
        with open(self.log_file_path, 'r', encoding='utf-8') as f:
            try:
                data = yaml.safe_load(f)
                return data if data is not None else []
            except yaml.YAMLError:
                return []

    def close(self) -> None:
        pass # No specific resources to close for file-based logger

    def __del__(self):
        """No-op as file handles are managed within methods."""
        pass


class OutputManager(IOutputManager):
    """
    A concrete implementation of IOutputManager that uses YamlOutputLogger
    to store and manage output for multiple processes.
    """

    def __init__(self, output_storage_path: str = "./command_outputs"):
        self.output_storage_path = output_storage_path
        os.makedirs(self.output_storage_path, exist_ok=True)
        self._loggers: Dict[str, Dict[str, YamlOutputLogger]] = {}
        self._shutdown_flag = False

    async def initialize(self) -> None:
        """Initializes the OutputManager. No-op for this implementation as setup is done in __init__."""
        pass

    def _get_log_file_path(self, process_id: str, output_key: str) -> str:
        return os.path.join(self.output_storage_path, f"{process_id}_{output_key}.yaml")

    def _get_logger_for_process(self, process_id: str, output_key: str) -> YamlOutputLogger:
        if self._shutdown_flag:
            raise Exception("OutputManager is shutting down or already shut down.")

        if process_id not in self._loggers:
            # Ensure the directory for the process exists
            process_dir = os.path.join(self.output_storage_path, process_id)
            os.makedirs(process_dir, exist_ok=True)
            self._loggers[process_id] = {}

        if output_key not in self._loggers[process_id]:
            log_file_path = self._get_log_file_path(process_id, output_key)
            self._loggers[process_id][output_key] = YamlOutputLogger(log_file_path)
        return self._loggers[process_id][output_key]

    async def store_output(self,
                           process_id: str,
                           output_key: str,
                           message: str | list[str],
                           timestamp: Optional[datetime] = None) -> None:
        if not process_id or not output_key:
            raise ValueError("Process ID and output key cannot be empty.")
        try:
            logger = self._get_logger_for_process(process_id, output_key)
            if isinstance(message, list):
                logger.add_messages(message, timestamp=timestamp)
            else:
                logger.add_message(message, timestamp=timestamp)
        except Exception as e:
            raise StorageError(f"Failed to store output for process {process_id}: {e}") from e

    async def get_output(self,
                         process_id: str,
                         output_key: str,
                         since: Optional[float] = None,
                         until: Optional[float] = None,
                         tail: Optional[int] = None,
                         grep_pattern: Optional[str] = None,
                         grep_mode: str = "line") -> AsyncGenerator[OutputMessageEntry, None]:
        if not process_id or not output_key:
            raise ValueError("Process ID and output key cannot be empty.")
        
        if process_id not in self._loggers or output_key not in self._loggers[process_id]:
            # If the process or output key doesn't exist, treat it as ProcessNotFoundError
            raise ProcessNotFoundError(f"Process with ID {process_id} or output key {output_key} not found.")

        try:
            logger = self._loggers[process_id][output_key]
            async for entry in logger.get_logs(tail=tail, since=datetime.fromtimestamp(since) if since else None, until=datetime.fromtimestamp(until) if until else None):
                if grep_pattern:
                    if grep_mode == "line":
                        if re.search(grep_pattern, entry.text):
                            yield OutputMessageEntry(timestamp=entry.timestamp, text=entry.text, output_key=output_key)
                    elif grep_mode == "content":
                        match = re.search(grep_pattern, entry.text)
                        if match:
                            yield OutputMessageEntry(timestamp=entry.timestamp, text=match.group(0), output_key=output_key)
                    else:
                        raise ValueError("grep_mode must be 'line' or 'content'.")
                else:
                    yield OutputMessageEntry(timestamp=entry.timestamp, text=entry.text, output_key=output_key)
        except Exception as e:
            raise OutputRetrievalError(f"Failed to retrieve output for process {process_id}: {e}") from e

    async def clear_output(self, process_id: str) -> None:
        if not process_id:
            raise ValueError("Process ID cannot be empty.")
        
        if process_id not in self._loggers:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")

        try:
            for output_key in list(self._loggers[process_id].keys()): # Iterate over a copy of keys
                logger = self._loggers[process_id][output_key]
                logger.close()
                log_file_path = self._get_log_file_path(process_id, output_key)
                if os.path.exists(log_file_path):
                    os.remove(log_file_path)
                del self._loggers[process_id][output_key]
            # Optionally remove the process_id directory if empty
            process_dir = os.path.join(self.output_storage_path, process_id)
            if os.path.exists(process_dir) and not os.listdir(process_dir):
                os.rmdir(process_dir)
            del self._loggers[process_id]
        except Exception as e:
            raise OutputClearError(f"Failed to clear output for process {process_id}: {e}") from e

    async def shutdown(self) -> None:
        self._shutdown_flag = True
        last_exception = None # Keep track of the last exception
        for process_id in list(self._loggers.keys()):
            for output_key in list(self._loggers[process_id].keys()):
                try:
                    logger = self._loggers[process_id][output_key]
                    logger.close()
                    log_file_path = self._get_log_file_path(process_id, output_key)
                    if os.path.exists(log_file_path):
                        os.remove(log_file_path)
                    del self._loggers[process_id][output_key]
                except Exception as e:
                    # Log the error but continue shutting down other loggers
                    print(f"Error during shutdown for process {process_id}, output {output_key}: {e}")
                    last_exception = e # Store the last exception
            process_dir = os.path.join(self.output_storage_path, process_id)
            if os.path.exists(process_dir) and not os.listdir(process_dir):
                os.rmdir(process_dir)
            if process_id in self._loggers: # This check is needed if inner loop removed all keys for a process_id
                del self._loggers[process_id]
        self._loggers.clear()
        # Optionally remove the root storage directory if empty
        if os.path.exists(self.output_storage_path) and not os.listdir(self.output_storage_path):
            try:
                os.rmdir(self.output_storage_path)
            except OSError as e:
                # Handle case where directory might not be empty due to hidden files or race conditions
                print(f"Warning: Could not remove output storage directory {self.output_storage_path}: {e}")
        if last_exception:
            raise last_exception
