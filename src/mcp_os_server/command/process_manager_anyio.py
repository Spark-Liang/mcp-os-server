import logging
import os
import shutil
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

import anyio
import psutil
from anyio import (
    Event,
    Lock,
    create_task_group,
    move_on_after,
    open_process,
)
from anyio.abc import Process, TaskGroup

from .exceptions import (
    CommandExecutionError,
    ProcessControlError,
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

logger = logging.getLogger(__name__)


async def terminate_windows_process(process: Process):
    """
    Terminate a Windows process.

    Note: On Windows, terminating a process with process.terminate() doesn't
    always guarantee immediate process termination.
    So we give it 2s to exit, or we call process.kill()
    which sends a SIGKILL equivalent signal.

    Args:
        process: The process to terminate
    """
    try:
        parent = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        logger.debug("process %s not found", process.pid)
        return

    try:
        logger.debug("try to terminate process tree from %s", process.pid)
        for child in parent.children(recursive=True):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                logger.debug("child process %s not found", child.pid)
                continue
        parent.terminate()
        with anyio.fail_after(2.0):
            await process.wait()
            return
    except Exception as e:
        logger.debug(
            "Failed to terminate process %s: %s", process.pid, str(e), exc_info=True
        )


async def terminate_process(process: Process):
    """
    Terminate a process gracefully.

    Args:
        process: The process to terminate

    Raises:
        ProcessControlError: If the process cannot be terminated
    """
    if sys.platform == "win32":
        await terminate_windows_process(process)
    else:
        process.terminate()


async def kill_windows_process(process: Process):
    """
    Kill a Windows process.
    """
    try:
        parent = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        logger.debug("process %s not found", process.pid)
        return

    try:
        logger.debug("try to kill process tree from %s", process.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                logger.debug("child process %s not found", child.pid)
                continue
        parent.kill()
        with anyio.fail_after(2.0):
            await process.wait()
            return
    except Exception as e:
        logger.debug(
            "Failed to kill process %s: %s", process.pid, str(e), exc_info=True
        )


async def kill_process(process: Process):
    """
    Kill a process.

    Args:
        process: The process to kill
    """
    logger.debug("获取调用方信息: \n%s", "\n".join(traceback.format_stack()))
    if sys.platform == "win32":
        await kill_windows_process(process)
    else:
        process.kill()


def get_windows_executable_command(command: str, path: str) -> str:
    """
    Get the correct executable command normalized for Windows.

    On Windows, commands might exist with specific extensions (.exe, .cmd, etc.)
    that need to be located for proper execution.

    Args:
        command: Base command (e.g., 'uvx', 'npx')

    Returns:
        str: Windows-appropriate command path
    """
    try:
        # First check if built-in command for cmd.exe, such as `echo`
        if command.lower() in ["echo", "dir", "type", "cls", "pause", "exit"]:
            return command

        # Then check if command exists in PATH as-is
        if command_path := shutil.which(command, path=path):
            return command_path

        # Check for Windows-specific extensions
        for ext in [".cmd", ".bat", ".exe", ".ps1"]:
            ext_version = f"{command}{ext}"
            if ext_path := shutil.which(ext_version, path=path):
                return ext_path

        # For regular commands or if we couldn't find special versions
        return command
    except OSError:
        # Handle file system errors during path resolution
        # (permissions, broken symlinks, etc.)
        return command


def _get_executable_command(command: str, path: str) -> str:
    """
    Get the correct executable command normalized for the current platform.

    Args:
        command: Base command (e.g., 'uvx', 'npx')
        path: The path to be added to the PATH environment variable
    Returns:
        str: Platform-appropriate command
    """
    if sys.platform == "win32":
        return get_windows_executable_command(command, path)
    else:
        return command


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
        timeout: int,
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
                tg.start_soon(self._wait_with_timeout)
                # 启动一个任务，每1秒检查一次进程是否还在运行
                tg.start_soon(self._check_process_running)
        except Exception as e:
            logger.debug("Exception in _run_monitoring: %s", str(e))
            await self._set_final_status(
                ProcessStatus.ERROR, None, f"Monitoring failed: {str(e)}"
            )

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
                        await self._output_manager.store_output(
                            self._pid, output_key, lines
                        )
                except anyio.EndOfStream:
                    # Stream正常结束
                    break
                except anyio.ClosedResourceError:
                    # Stream已关闭
                    logger.debug("Stream %s closed", output_key, exc_info=True)
                    break
                except Exception as e:
                    logger.debug(
                        "Exception in _read_stream for %s: %s", output_key, str(e)
                    )
                    # 其他异常，记录但不中断
                    await self._output_manager.store_output(
                        self._pid, output_key, [f"Stream read error: {str(e)}"]
                    )
                    break
        except Exception as e:
            logger.debug(
                "Outer exception in _read_stream for %s: %s",
                output_key,
                str(e),
                exc_info=True,
            )
            # 静默处理任何其他异常，避免监控任务崩溃
            pass

    async def _check_process_running(self):
        while True:
            try:
                with anyio.move_on_after(1):
                    await self._process.wait()
                    break
                logger.debug("process %s is still running", self._pid)
            except anyio.ClosedResourceError:
                break
            except anyio.EndOfStream:
                break
            except Exception as e:
                logger.debug(
                    "Exception in _check_process_running: %s", str(e), exc_info=True
                )
                break

    async def _wait_with_timeout(self):
        try:
            with move_on_after(float(self._timeout)):
                exit_code = await self._process.wait()
                async with self._lock:
                    if self._status == ProcessStatus.TERMINATED:
                        status = ProcessStatus.TERMINATED
                        error = self._error_message
                    elif exit_code == 0:
                        status = ProcessStatus.COMPLETED
                        error = None
                    else:
                        status = ProcessStatus.FAILED
                        error = None
                await self._set_final_status(status, exit_code, error)
                return
            # Timeout block
            error_msg = "Process timed out"
            try:
                await terminate_process(self._process)
            except Exception as e:
                logger.debug("Failed to terminate timed out process: %s", str(e))
                await kill_process(self._process)
                error_msg = "Process timed out (killed)"
            # Wait for process to exit
            with move_on_after(5.0):
                exit_code = await self._process.wait()
                await self._set_final_status(
                    ProcessStatus.TERMINATED, exit_code, error_msg
                )
                return
            # If still not exited
            await self._set_final_status(
                ProcessStatus.ERROR, None, "Process timed out (failed to terminate)"
            )
        except Exception as e:
            logger.debug("Exception in _wait_with_timeout: %s", str(e), exc_info=True)
            await self._set_final_status(
                ProcessStatus.ERROR, None, f"Wait failed: {str(e)}"
            )

    async def _set_final_status(
        self,
        status: ProcessStatus,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
        already_acquired_lock: bool = False,
    ) -> None:
        if not ProcessStatus.is_final(status):
            # 如果状态不是最终状态，则不设置
            logger.warning("内部错误: 状态不是最终状态: %s", status)
            return

        def action():
            self._status = status
            self._exit_code = exit_code
            self._error_message = error_message
            self._end_time = datetime.now()
            self._completion_event.set()

        if not already_acquired_lock:
            async with self._lock:
                action()
        else:
            action()

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
                encoding=self._encoding,
                envs=self._envs,
            )

    async def wait_for_completion(self, timeout: Optional[int] = None) -> ProcessInfo:
        if timeout is not None:
            with move_on_after(timeout):
                await self._completion_event.wait()
                info = await self.get_details()
                # Check if process terminated due to internal timeout
                if (
                    info.status == ProcessStatus.TERMINATED
                    and info.error_message
                    and "timed out" in info.error_message.lower()
                ):
                    raise ProcessTimeoutError("Process timed out")
                return info
            raise ProcessTimeoutError("Wait for completion timed out")
        else:
            await self._completion_event.wait()
            info = await self.get_details()
            # Check if process terminated due to internal timeout
            if (
                info.status == ProcessStatus.TERMINATED
                and info.error_message
                and "timed out" in info.error_message.lower()
            ):
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
        logger.debug(
            "try to stop process %s with force: %s, reason: %s",
            self._pid,
            force,
            reason,
        )
        if self._status != ProcessStatus.RUNNING:
            logger.warning("Process %s is not running, skip stop", self._pid)
            return
        async with self._lock:
            if self._status != ProcessStatus.RUNNING:
                raise ProcessControlError("Process is not running")
            try:
                if force:
                    await kill_process(self._process)
                else:
                    await terminate_process(self._process)
                    try:
                        with anyio.fail_after(2.0):
                            await self._process.wait()
                    except Exception as e:
                        logger.warning(
                            "Failed to wait for process to terminate: %s",
                            str(e),
                            exc_info=True,
                        )
                        await kill_process(self._process)

            except Exception as e:
                logger.debug("Exception in stop: %s", str(e))
                raise ProcessControlError(f"Failed to stop process: {str(e)}")
            finally:
                await self._set_final_status(
                    ProcessStatus.TERMINATED,
                    None,
                    reason or "Stopped by user",
                    already_acquired_lock=True,
                )

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
                            if (
                                info.status
                                in (
                                    ProcessStatus.COMPLETED,
                                    ProcessStatus.FAILED,
                                    ProcessStatus.TERMINATED,
                                    ProcessStatus.ERROR,
                                )
                                and info.end_time
                                and (datetime.now() - info.end_time).total_seconds()
                                > self.process_retention_seconds
                            ):
                                processes_to_clean.append((pid, p))
                        except Exception as e:
                            logger.debug(
                                "Exception getting details in _auto_cleaner for %s: %s",
                                pid,
                                str(e),
                            )
                            continue

                # 在锁外清理进程输出，然后在锁内移除进程
                async with create_task_group() as tg:

                    async def clean_one(pid, p):
                        try:
                            await p._output_manager.clear_output(pid)
                            async with p._lock:
                                p._cleaned = True
                        except Exception as e:
                            logger.warning(
                                "Failed to clean process %s in _auto_cleaner: %s",
                                pid,
                                str(e),
                            )

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
        timeout: int,
        stdin_data: Optional[bytes | str] = None,
        envs: Optional[Dict[str, str | None]] = None,
        encoding: str = sys.getdefaultencoding(),
        labels: Optional[List[str]] = None,
        extra_paths: Optional[List[str | Path]] = None,
    ) -> IProcess:
        if not command:
            raise CommandExecutionError("Command is empty")

        logger.debug("directory: %s", directory)
        directory_path = Path(directory)
        if not directory_path.is_absolute():
            directory_path = directory_path.resolve()
        if not directory_path.exists():
            raise CommandExecutionError(f"Directory {directory} (absolute path: {directory_path}) does not exist")
        if not directory_path.is_dir():
            raise CommandExecutionError(f"Directory {directory} (absolute path: {directory_path}) is not a directory")
        directory = str(directory_path)
        logger.debug("resolved directory: %s", directory)

        env = {k: v for k, v in {**(envs or {})}.items() if v is not None}
        path_env_var = env.get("PATH", os.environ.get("PATH", ""))
        if extra_paths:
            path_env_var = os.pathsep.join(
                [*[str(p) for p in extra_paths], path_env_var]
            )
        env["PATH"] = path_env_var
        try:
            logger.debug("original command: %s", command)
            executable_command = _get_executable_command(command[0], path=path_env_var)
            command = [executable_command] + command[1:]
            logger.debug("normalized command: %s", command)

            logger.debug(
                f"Starting process:\ncommand: {command}\ndirectory: {directory}\nencoding: {encoding}\nenv: {env}"
            )
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW
                try:
                    process = await open_process(
                        command,
                        cwd=directory,
                        env=env,
                        stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=creation_flags,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to open process with creation flags: %s", str(e)
                    )
                    process = await open_process(
                        command,
                        cwd=directory,
                        env=env,
                        stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
            else:
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
                try:
                    await terminate_process(process)
                except Exception as e:
                    logger.warning("Failed to terminate process: %s", str(e))
                    try:
                        await kill_process(process)
                    except Exception as e:
                        logger.warning("Failed to kill process: %s", str(e))

                raise CommandExecutionError(
                    "Failed to generate unique PID after 10 attempts"
                )

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
                        raise CommandExecutionError(
                            f"Failed to encode stdin_data with {encoding}: {str(e)}"
                        ) from e
                if process.stdin is not None:
                    try:
                        await process.stdin.send(stdin_data)
                        await process.stdin.aclose()
                    except Exception as e:
                        raise CommandExecutionError(
                            f"Failed to write stdin_data to subprocess: {str(e)}"
                        ) from e
            except CommandExecutionError:
                logger.error(
                    "Failed to write stdin_data to subprocess, force stop process",
                    exc_info=True,
                )
                # 如果stdin处理失败，关闭进程并抛出异常
                await anyio_process.stop(
                    force=True, reason="Failed to write stdin_data to subprocess"
                )
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
                    logger.debug(
                        "Exception getting details in list_processes for %s: %s",
                        pid,
                        str(e),
                    )

            for pid, p in process_list:
                tg.start_soon(get_info, pid, p)

        result = []
        for pid, _ in process_list:
            info = pid_to_info.get(pid)
            if (
                info
                and (status is None or info.status == status)
                and (labels is None or set(labels).issubset(set(info.labels)))
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
                info = (
                    await p.get_details()
                )  # Note: get_details inside lock, but it's fast
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
        logger.debug("shutdown called from \n%s", "\n".join(traceback.format_stack()))
        # 设置关闭事件，停止自动清理任务
        self._shutdown_event.set()

        # 停止主task group
        if self._main_tg:
            try:
                self._main_tg.cancel_scope.cancel()
                await self._main_tg.__aexit__(None, None, None)
                self._main_tg = None
            except Exception as e:
                logger.debug("Exception closing task group in shutdown: %s", str(e))
                pass

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
        logger.debug("shutdown completed")

    async def _safe_stop_process(self, process: AnyioProcess) -> None:
        """安全地停止进程，捕获所有异常"""
        try:
            await process.stop(force=True, reason="shutdown manager")
        except Exception as e:
            logger.debug("Exception in _safe_stop_process: %s", str(e), exc_info=True)
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
