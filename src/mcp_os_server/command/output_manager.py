from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

import yaml

from .exceptions import (
    OutputClearError,
    OutputRetrievalError,
    ProcessNotFoundError,
    StorageError,
)
from .interfaces import IOutputManager, OutputMessageEntry


class OutputManager(IOutputManager):
    """
    Manages process output by storing logs in YAML files.

    Each process gets a dedicated directory named after its process ID.
    Inside this directory, 'stdout.yaml' and 'stderr.yaml' files store
    the corresponding output streams.
    """

    def __init__(self, base_log_path: Path):
        self._base_log_path = base_log_path
        self._base_log_path.mkdir(parents=True, exist_ok=True)
        self._write_locks: dict[Path, asyncio.Lock] = {}

    def _get_process_log_path(self, process_id: str) -> Path:
        return self._base_log_path / process_id

    def _get_log_file_path(self, process_id: str, output_key: str) -> Path:
        return self._get_process_log_path(process_id) / f"{output_key}.yaml"

    async def _get_lock(self, file_path: Path) -> asyncio.Lock:
        if file_path not in self._write_locks:
            self._write_locks[file_path] = asyncio.Lock()
        return self._write_locks[file_path]

    async def store_output(
        self, process_id: str, output_key: str, message: str | list[str]
    ) -> None:
        if not process_id or not output_key:
            raise ValueError("Process ID and output key cannot be empty.")

        log_path = self._get_process_log_path(process_id)
        log_path.mkdir(exist_ok=True)
        file_path = self._get_log_file_path(process_id, output_key)
        lock = await self._get_lock(file_path)

        messages = [message] if isinstance(message, str) else message
        entries = [
            {
                "timestamp": time.time(),
                "text": msg,
                "source": output_key,
            }
            for msg in messages
        ]

        async with lock:
            try:
                with file_path.open("a", encoding="utf-8") as f:
                    # Use safe_dump for security and add a document separator
                    yaml.safe_dump_all(entries, f, default_flow_style=False)
            except Exception as e:
                raise StorageError(f"Failed to store output for {process_id}: {e}") from e

    async def get_output(
        self,
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        if not process_id or not output_key:
            raise ValueError("Process ID and output key cannot be empty.")

        file_path = self._get_log_file_path(process_id, output_key)
        if not file_path.exists():
            # It's not an error for a process to have no output
            return

        lock = await self._get_lock(file_path)
        
        async with lock:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    # safe_load_all returns a generator
                    all_logs = list(yaml.safe_load_all(f))
            except Exception as e:
                raise OutputRetrievalError(
                    f"Failed to get output for {process_id}: {e}"
                ) from e

        # This part is synchronous as it just processes the loaded data
        if since:
            all_logs = [log for log in all_logs if log["timestamp"] >= since]
        if until:
            all_logs = [log for log in all_logs if log["timestamp"] <= until]
        if tail:
            all_logs = all_logs[-tail:]

        for log_data in all_logs:
            yield OutputMessageEntry(**log_data)

    async def clear_output(self, process_id: str) -> None:
        if not process_id:
            raise ValueError("Process ID cannot be empty.")

        process_path = self._get_process_log_path(process_id)
        if not process_path.exists():
            raise ProcessNotFoundError(f"Log directory for process {process_id} not found.")

        try:
            for log_file in process_path.glob("*.yaml"):
                lock = await self._get_lock(log_file)
                async with lock:
                    log_file.unlink()
                # Clean up lock from memory
                if log_file in self._write_locks:
                    del self._write_locks[log_file]

            process_path.rmdir()
        except Exception as e:
            raise OutputClearError(f"Failed to clear output for {process_id}: {e}") from e

    async def shutdown(self) -> None:
        # No specific shutdown actions needed for this file-based implementation
        pass 