from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncGenerator, Dict, List, Optional

from .exceptions import CommandTimeoutError, ProcessNotFoundError, ProcessTimeoutError
from .interfaces import (
    ICommandExecutor,
    IProcess,
    IProcessManager,
    OutputMessageEntry,
    ProcessInfo,
    ProcessStatus,
)
from .models import CommandResult


class CommandExecutor(ICommandExecutor):
    def __init__(
        self,
        process_manager: IProcessManager,
        default_encoding: str = "utf-8",
        limit_lines: int = 500,
    ):
        self._process_manager = process_manager
        self._default_encoding = default_encoding
        self._limit_lines = limit_lines

    async def initialize(self) -> None:
        await self._process_manager.initialize()

    async def execute_command(
        self,
        command: List[str],
        directory: str,
        stdin_data: Optional[bytes] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        limit_lines: Optional[int] = None,
    ) -> CommandResult:
        start_time = time.monotonic()
        
        # Prepare environment
        final_envs = os.environ.copy()
        if envs:
            final_envs.update(envs)
        
        used_encoding = encoding or self._default_encoding
        if used_encoding:
            final_envs['PYTHONIOENCODING'] = used_encoding

        process = await self._process_manager.start_process(
            command=command,
            directory=directory,
            description=f"Synchronous execution of {' '.join(command)}",
            stdin_data=stdin_data,
            timeout=timeout,
            envs=final_envs,
            encoding=used_encoding,
        )

        try:
            completed_info = await process.wait_for_completion(timeout=timeout)
        except (asyncio.TimeoutError, ProcessTimeoutError) as e:
            # 超时时收集已有的输出
            tail_limit = limit_lines if limit_lines is not None else None
            
            stdout_output = process.get_output("stdout", tail=tail_limit)
            if asyncio.iscoroutine(stdout_output):
                stdout_output = await stdout_output
            stdout_lines = [log.text async for log in stdout_output]
            
            stderr_output = process.get_output("stderr", tail=tail_limit)
            if asyncio.iscoroutine(stderr_output):
                stderr_output = await stderr_output
            stderr_lines = [log.text async for log in stderr_output]
            
            raise CommandTimeoutError(
                f"Command '{' '.join(command)}' timed out.",
                pid=process.pid,
                stdout="\n".join(stdout_lines),
                stderr="\n".join(stderr_lines)
            ) from e

        end_time = time.monotonic()

        # Collect output
        # 如果明确指定了 limit_lines，则使用 tail 限制输出行数
        # 否则获取所有输出
        tail_limit = limit_lines if limit_lines is not None else None
        
        stdout_output = process.get_output("stdout", tail=tail_limit)
        if asyncio.iscoroutine(stdout_output):
            stdout_output = await stdout_output
        stdout_lines = [log.text async for log in stdout_output]

        stderr_output = process.get_output("stderr", tail=tail_limit)
        if asyncio.iscoroutine(stderr_output):
            stderr_output = await stderr_output
        stderr_lines = [log.text async for log in stderr_output]

        return CommandResult(
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
            exit_code=completed_info.exit_code if completed_info.exit_code is not None else -1,
            execution_time=end_time - start_time,
        )

    async def start_background_command(
        self,
        command: List[str],
        directory: str,
        description: str,
        stdin_data: Optional[bytes] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> IProcess:
        # Prepare environment
        final_envs = os.environ.copy()
        if envs:
            final_envs.update(envs)

        used_encoding = encoding or self._default_encoding
        if used_encoding:
            final_envs['PYTHONIOENCODING'] = used_encoding

        return await self._process_manager.start_process(
            command=command,
            directory=directory,
            description=description,
            stdin_data=stdin_data,
            timeout=timeout,
            envs=final_envs,
            encoding=used_encoding,
            labels=labels,
        )

    async def get_process_logs(
        self,
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        process = await self._process_manager.get_process(process_id)
        logs_output = process.get_output(output_key, since, until, tail)
        if asyncio.iscoroutine(logs_output):
            logs_output = await logs_output
        async for log in logs_output:
            yield log

    async def stop_process(
        self, process_id: str, force: bool = False, reason: Optional[str] = None
    ) -> None:
        await self._process_manager.stop_process(process_id, force=force, reason=reason)

    async def list_process(
        self, status: Optional[ProcessStatus] = None, labels: Optional[List[str]] = None
    ) -> List[ProcessInfo]:
        return await self._process_manager.list_processes(status, labels)

    async def get_process_detail(self, process_id: str) -> ProcessInfo:
        return await self._process_manager.get_process_info(process_id)

    async def clean_process(self, process_ids: List[str]) -> Dict[str, str]:
        return await self._process_manager.clean_processes(process_ids)

    async def shutdown(self) -> None:
        await self._process_manager.shutdown()

    async def get_process_manager(self) -> IProcessManager:
        return self._process_manager 