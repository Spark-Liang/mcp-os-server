import uuid
from datetime import datetime
import sys

import anyio
from anyio import (
    Event,
    Lock,
    create_task_group,
    move_on_after,
    open_process,
    sleep,
    CancelScope,
)
from anyio.abc import Process, TaskGroup

import os
from typing import AsyncGenerator, Dict, List, Optional

from .exceptions import (
    CommandExecutionError,
    OutputRetrievalError,
    ProcessControlError,
    ProcessInfoRetrievalError,
    ProcessListRetrievalError,
    ProcessNotFoundError,
    ProcessTimeoutError,
)
from .interfaces import (
    IOutputManager,
    IProcess,
    IProcessManager,
    OutputMessageEntry,
    ProcessInfo,
    ProcessStatus,
)

import subprocess

import logging

logger = logging.getLogger(__name__)


class AnyioProcess(IProcess):
    def __init__(
        self,
        pid: str,
        process: Process,
        output_manager: IOutputManager,
        command: list[str],
        directory: str,
        description: str,
        labels: list[str],
        timeout: Optional[int],
        encoding: str,
        envs: Dict[str, str],
        start_time: datetime,
    ):
        self._pid = pid
        self._process = process
        self._output_manager = output_manager
        self._command = command
        self._directory = directory
        self._description = description
        self._labels = labels
        self._timeout = timeout
        self._encoding = encoding
        self._envs = envs
        self._start_time = start_time

        self._status = ProcessStatus.RUNNING
        self._end_time: Optional[datetime] = None
        self._exit_code: Optional[int] = None
        self._error_message: Optional[str] = None

        self._completion_event = Event()
        self._lock = Lock()
        self._tg: Optional[TaskGroup] = None
        self._cancel_scope = None
        self._cleaned = False

    @property
    def pid(self) -> str:
        return self._pid

    @property
    def cleaned(self) -> bool:
        return self._cleaned

    async def _run_monitoring(self):
        try:
            async with create_task_group() as tg:
                tg.start_soon(self._read_stream, self._process.stdout, "stdout")
                tg.start_soon(self._read_stream, self._process.stderr, "stderr")
                tg.start_soon(self._wait_process)
                if self._timeout:
                    tg.start_soon(self._handle_timeout)
        except Exception as e:
            logger.debug("Exception in _run_monitoring: %s", str(e))
            # 如果监控过程出现异常，设置进程状态为错误
            async with self._lock:
                if self._status == ProcessStatus.RUNNING:
                    self._status = ProcessStatus.ERROR
                    self._error_message = f"Monitoring failed: {str(e)}"
                    self._end_time = datetime.now()
            self._completion_event.set()

    async def _read_stream(self, stream, output_key: str):
        try:
            while True:
                try:
                    chunk = await stream.receive(4096)
                    if not chunk:
                        break
                    text = chunk.decode(self._encoding, errors="replace")
                    lines = text.splitlines()
                    if lines:  # 只有当有内容时才存储
                        await self._output_manager.store_output(self._pid, output_key, lines)
                except anyio.EndOfStream:
                    # Stream正常结束
                    break
                except anyio.ClosedResourceError:
                    # Stream已关闭
                    break
                except Exception as e:
                    logger.debug("Exception in _read_stream for %s: %s", output_key, str(e))
                    # 其他异常，记录但不中断
                    await self._output_manager.store_output(
                        self._pid, output_key, [f"Stream read error: {str(e)}"]
                    )
                    break
        except Exception as e:
            logger.debug("Outer exception in _read_stream for %s: %s", output_key, str(e))
            # 静默处理任何其他异常，避免监控任务崩溃
            pass

    async def _wait_process(self):
        try:
            exit_code = await self._process.wait()
            async with self._lock:
                self._end_time = datetime.now()
                self._exit_code = exit_code
                # 检查是否已经被主动停止或超时
                if self._status == ProcessStatus.TERMINATED:
                    # 已经被标记为TERMINATED，保持这个状态
                    pass
                elif exit_code == 0:
                    self._status = ProcessStatus.COMPLETED
                else:
                    self._status = ProcessStatus.FAILED
        except Exception as e:
            logger.debug("Exception in _wait_process: %s", str(e))
            async with self._lock:
                self._end_time = datetime.now()
                self._status = ProcessStatus.ERROR
                self._error_message = f"Wait failed: {str(e)}"
        finally:
            self._completion_event.set()

    async def _handle_timeout(self):
        assert self._timeout is not None
        await sleep(float(self._timeout))
        async with self._lock:
            if self._status == ProcessStatus.RUNNING:
                try:
                    self._process.terminate()
                    self._error_message = "Process timed out"
                    self._status = ProcessStatus.TERMINATED
                except Exception as e:
                    # 如果terminate失败，尝试kill
                    try:
                        self._process.kill()
                        self._error_message = "Process timed out (killed)"
                        self._status = ProcessStatus.TERMINATED
                    except Exception as e:
                        logger.debug("Failed to kill timed out process: %s", str(e))
                        # 如果kill也失败，标记为错误状态
                        self._status = ProcessStatus.ERROR
                        self._error_message = "Process timed out (failed to terminate)"

    async def get_details(self) -> ProcessInfo:
        async with self._lock:
            return ProcessInfo(
                pid=self._pid,
                command=self._command,
                directory=self._directory,
                description=self._description,
                status=self._status,
                start_time=self._start_time,
                end_time=self._end_time,
                exit_code=self._exit_code,
                labels=self._labels,
                timeout=self._timeout,
                error_message=self._error_message,
                envs=self._envs,
            )

    async def wait_for_completion(self, timeout: Optional[int] = None) -> ProcessInfo:
        if timeout is not None:
            with move_on_after(timeout):
                await self._completion_event.wait()
                info = await self.get_details()
                # Check if process terminated due to internal timeout
                if (info.status == ProcessStatus.TERMINATED and 
                    info.error_message and "timed out" in info.error_message.lower()):
                    raise ProcessTimeoutError("Process timed out")
                return info
            raise ProcessTimeoutError("Wait for completion timed out")
        else:
            await self._completion_event.wait()
            info = await self.get_details()
            # Check if process terminated due to internal timeout
            if (info.status == ProcessStatus.TERMINATED and 
                info.error_message and "timed out" in info.error_message.lower()):
                raise ProcessTimeoutError("Process timed out")
            return info

    async def get_output(
        self,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        async for entry in self._output_manager.get_output(
            self._pid, output_key, since, until, tail
        ):
            yield entry

    async def stop(self, force: bool = False, reason: Optional[str] = None) -> None:
        async with self._lock:
            if self._status != ProcessStatus.RUNNING:
                raise ProcessControlError("Process is not running")
            try:
                if force:
                    self._process.kill()
                else:
                    self._process.terminate()
                self._error_message = reason or "Stopped by user"
                self._status = ProcessStatus.TERMINATED
            except Exception as e:
                logger.debug("Exception in stop: %s", str(e))
                raise ProcessControlError(f"Failed to stop process: {str(e)}")

    async def clean(self) -> Optional[str]:
        await self._output_manager.clear_output(self._pid)
        async with self._lock:
            if self._status == ProcessStatus.RUNNING:
                return "Failed: Process is still running"
            self._cleaned = True
        return None


class AnyioProcessManager(IProcessManager):
    def __init__(
        self, output_manager: IOutputManager, process_retention_seconds: int = 3600
    ):
        self._output_manager = output_manager
        self._processes: Dict[str, AnyioProcess] = {}
        self._lock = Lock()
        self.process_retention_seconds = process_retention_seconds
        self._main_tg = None
        self._shutdown_event = Event()

    async def initialize(self) -> None:
        # 创建并启动后台任务组来管理自动清理任务
        self._main_tg = anyio.create_task_group()
        await self._main_tg.__aenter__()
        self._main_tg.start_soon(self._auto_cleaner)

    async def _auto_cleaner(self):
        try:
            while not self._shutdown_event.is_set():
                # 等待1秒，如果在此期间收到shutdown信号则退出
                with anyio.move_on_after(1):
                    await self._shutdown_event.wait()
                    break  # 收到shutdown信号，退出循环
                
                # 如果move_on_after超时（1秒内没有收到shutdown信号），继续执行清理任务
                if self._shutdown_event.is_set():
                    break
                
                # 执行清理任务
                processes_to_clean = []
                async with self._lock:
                    for pid, p in list(self._processes.items()):
                        try:
                            info = await p.get_details()
                            if (info.status in (ProcessStatus.COMPLETED, ProcessStatus.FAILED, 
                                              ProcessStatus.TERMINATED, ProcessStatus.ERROR) 
                                and info.end_time 
                                and (datetime.now() - info.end_time).total_seconds() > self.process_retention_seconds):
                                processes_to_clean.append((pid, p))
                        except Exception as e:
                            logger.debug("Exception getting details in _auto_cleaner for %s: %s", pid, str(e))
                            continue
                
                # 在锁外清理进程输出，然后在锁内移除进程
                async with create_task_group() as tg:
                    async def clean_one(pid, p):
                        try:
                            await p._output_manager.clear_output(pid)
                            async with p._lock:
                                p._cleaned = True
                        except Exception as e:
                            logger.warning("Failed to clean process %s in _auto_cleaner: %s", pid, str(e))

                    for pid, process in processes_to_clean:
                        tg.start_soon(clean_one, pid, process)
                
                async with self._lock:
                    for pid, _ in processes_to_clean:
                        if pid in self._processes:
                            del self._processes[pid]
        except Exception as e:
            logger.warning("Exception in _auto_cleaner: %s", str(e))
            # 静默处理自动清理任务的异常，避免影响主程序
            pass

    async def start_process(
        self,
        command: List[str],
        directory: str,
        description: str,
        stdin_data: Optional[bytes | str] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: str = sys.getdefaultencoding(),
        labels: Optional[List[str]] = None,
    ) -> IProcess:
        env = {**os.environ, **(envs or {})}
        try:
            process = await open_process(
                command,
                cwd=directory,
                env=env,
                stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            logger.debug("Failed to open process: %s", str(e))
            raise CommandExecutionError(f"Failed to start process: {e}") from e

        
        async with self._lock:
            # 使用前5位UUID作为PID，避免PID过长。并且检查PID是否已经存在，如果存在，则重新生成，最多重试10次
            for _ in range(10):
                pid = str(uuid.uuid4())[:5]
                if pid not in self._processes:
                    break
                else:
                    logger.debug("Process with PID %s already exists, retrying...", pid)
            if pid in self._processes:
                raise CommandExecutionError(f"Failed to generate unique PID after 10 attempts")

            start_time = datetime.now()
            anyio_process = AnyioProcess(
                pid,
                process,
                self._output_manager,
                command,
                directory,
                description,
                labels or [],
                timeout,
                encoding,
                env,
                start_time,
            )

            self._processes[pid] = anyio_process
        
        # 确保main_tg存在才启动监控任务
        if self._main_tg is not None:
            self._main_tg.start_soon(anyio_process._run_monitoring)

        # 处理stdin数据
        if stdin_data:
            try:
                if isinstance(stdin_data, str):
                    try:
                        stdin_data = stdin_data.encode(encoding)
                    except UnicodeEncodeError as e:
                        raise CommandExecutionError(f"Failed to encode stdin_data with {encoding}: {str(e)}") from e
                if process.stdin is not None:
                    try:
                        await process.stdin.send(stdin_data)
                        await process.stdin.aclose()
                    except Exception as e:
                        raise CommandExecutionError(f"Failed to write stdin_data to subprocess: {str(e)}") from e
            except CommandExecutionError:
                # 如果stdin处理失败，关闭进程并抛出异常
                await anyio_process.stop(force=True)
                raise

        return anyio_process

    async def stop_process(
        self, process_id: str, force: bool = False, reason: Optional[str] = None
    ) -> None:
        async with self._lock:
            if process_id not in self._processes:
                raise ProcessNotFoundError(f"Process {process_id} not found")
            await self._processes[process_id].stop(force, reason)

    async def get_process_info(self, process_id: str) -> ProcessInfo:
        async with self._lock:
            if process_id not in self._processes:
                raise ProcessNotFoundError(f"Process {process_id} not found")
            p = self._processes[process_id]
            if p.cleaned:
                del self._processes[process_id]
                raise ProcessNotFoundError(f"Process {process_id} has been cleaned")
        return await p.get_details()

    async def list_processes(
        self, status: Optional[ProcessStatus] = None, labels: Optional[List[str]] = None
    ) -> List[ProcessInfo]:
        process_list = []
        to_remove = []
        async with self._lock:
            for pid, p in list(self._processes.items()):
                if p.cleaned:
                    to_remove.append(pid)
                    continue
                process_list.append((pid, p))
            for pid in to_remove:
                del self._processes[pid]

        pid_to_info = {}
        async with create_task_group() as tg:
            async def get_info(pid, p):
                try:
                    info = await p.get_details()
                    pid_to_info[pid] = info
                except Exception as e:
                    logger.debug("Exception getting details in list_processes for %s: %s", pid, str(e))

            for pid, p in process_list:
                tg.start_soon(get_info, pid, p)

        result = []
        for pid, _ in process_list:
            info = pid_to_info.get(pid)
            if info and (status is None or info.status == status) and (
                labels is None or set(labels).issubset(set(info.labels))
            ):
                result.append(info)

        return result

    async def clean_processes(self, process_ids: List[str]) -> Dict[str, Optional[str]]:
        results = {}
        processes_to_clean = {}
        async with self._lock:
            for pid in process_ids:
                if pid not in self._processes:
                    results[pid] = "Not found"
                    continue
                p = self._processes[pid]
                info = await p.get_details()  # Note: get_details inside lock, but it's fast
                if info.status == ProcessStatus.RUNNING:
                    results[pid] = "Failed: Process is still running"
                    continue
                processes_to_clean[pid] = p

        async with create_task_group() as tg:
            async def clean_one(pid, p):
                try:
                    res = await p.clean()
                    results[pid] = res
                except Exception as e:
                    results[pid] = f"Failed: {str(e)}"

            for pid, p in processes_to_clean.items():
                tg.start_soon(clean_one, pid, p)

        async with self._lock:
            for pid in list(results.keys()):
                if results[pid] is None:  # Success
                    if pid in self._processes:
                        del self._processes[pid]

        return results

    async def shutdown(self) -> None:
        # 设置关闭事件，停止自动清理任务
        self._shutdown_event.set()
        
        # 停止所有进程
        try:
            async with create_task_group() as tg:
                async with self._lock:
                    for p in list(self._processes.values()):
                        tg.start_soon(self._safe_stop_process, p)
        except Exception as e:
            logger.debug("Exception stopping processes in shutdown: %s", str(e))
            # 即使停止进程失败，也要继续清理
            pass
        
        # 清理所有进程
        try:
            async with create_task_group() as tg:
                async with self._lock:
                    for p in list(self._processes.values()):
                        tg.start_soon(self._safe_clean_process, p)
        except Exception as e:
            logger.debug("Exception cleaning processes in shutdown: %s", str(e))
            # 即使清理失败，也要继续关闭task group
            pass
        
        # 关闭主task group
        if self._main_tg:
            try:
                self._main_tg.cancel_scope.cancel()
                await self._main_tg.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("Exception closing task group in shutdown: %s", str(e))
                pass
            finally:
                self._main_tg = None

    async def _safe_stop_process(self, process: AnyioProcess) -> None:
        """安全地停止进程，捕获所有异常"""
        try:
            await process.stop(force=True)
        except Exception as e:
            logger.debug("Exception in _safe_stop_process: %s", str(e))
            pass

    async def _safe_clean_process(self, process: AnyioProcess) -> None:
        """安全地清理进程，捕获所有异常"""
        try:
            await process.clean()
        except Exception as e:
            logger.warning("Failed to clean process in _safe_clean_process: %s", str(e))
            pass

    async def get_process(self, process_id: str) -> IProcess:
        async with self._lock:
            if process_id not in self._processes:
                raise ProcessNotFoundError(f"Process {process_id} not found")
            p = self._processes[process_id]
            if p.cleaned:
                del self._processes[process_id]
                raise ProcessNotFoundError(f"Process {process_id} has been cleaned")
            return p


__all__ = ["AnyioProcessManager"]