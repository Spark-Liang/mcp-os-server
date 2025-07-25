from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
import shutil
import random
import string
import subprocess
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, List, Optional, Any, Tuple, cast, BinaryIO
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass

from .exceptions import (
    CommandExecutionError,
    ProcessCleanError,
    ProcessControlError,
    ProcessInfoRetrievalError,
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


class PriorityEnum(int,Enum):
    """
    优先级枚举类。数值越小，优先级越高。
    """
    PLATFORM_VARIANT_LEVEL = 10
    """系统细分变种，用于区分不同系统细分变种的优先级"""
    
    PLATFORM_LEVEL = 100
    """系统级别，用于区分不同系统的优先级"""
    
    DEFAULT_LEVEL = sys.maxsize
    """默认级别，用于没有明确指定优先级的情况"""


@dataclass
class PopenBuildArgs:
    """
    用于构建 subprocess.Popen 参数的 dataclass。

    Attributes:
        command (List[str]): 原始命令列表。
        directory (str): 工作目录。
        encoding (str): 用于编码/解码进程I/O的编码。
        envs (Dict[str, str]): 进程环境变量。
        stdin_data (Optional[bytes]): 要发送到标准输入的数据（已统一为字节类型）。
    """
    command: List[str]
    directory: str
    encoding: str
    envs: Dict[str, str]
    stdin_data: Optional[bytes]


class ISubprocessProcessFactory(ABC):
    """
    Interface for creating subprocess.Popen instances.
    定义用于适配不同平台创建 subprocess.Popen 逻辑的接口。
    """

    @abstractmethod
    def support_platform(self) -> Optional[int]:
        """
        返回是否支持当前平台，如果支持，返回支持的优先级，数值越小，优先级越高。
        如果不支持，返回 None。

        Returns:
            Optional[int]: 支持的优先级，数值越小，优先级越高。

        See Also:
            PriorityEnum
        """
        pass
    
    @abstractmethod
    async def create_process(
        self,
        command: List[str],
        directory: str,
        encoding: str,
        envs: Dict[str, str],
        stdin_data: Optional[bytes | str],
    ) -> subprocess.Popen:
        """
        Create a subprocess.Popen instance.
        创建 subprocess.Popen 实例。

        Args:
            command: 命令列表。
            directory: 工作目录。
            encoding: 编码。
            envs: 环境变量。
            stdin_data: 标准输入数据。

        Returns:
            subprocess.Popen: 创建的 subprocess.Popen 实例。

        Raises:
            CommandExecutionError: 命令执行错误。
        """
        pass


class BaseSubprocessProcessFactory(ISubprocessProcessFactory, ABC):
    """
    Base class for subprocess.Popen factories, containing common logic.
    """
    async def create_process(
        self,
        command: List[str],
        directory: str,
        encoding: str,
        envs: Dict[str, str],
        stdin_data: Optional[bytes | str],
    ) -> subprocess.Popen:
        normalized_directory = os.path.abspath(directory)
        if not os.path.isdir(normalized_directory):
            raise CommandExecutionError(f"Directory not found: {directory}")

        process_envs = os.environ.copy()
        process_envs.update(envs)

        original_command = command.copy()
        
        effective_stdin_data: Optional[bytes] = None
        if stdin_data:
            if isinstance(stdin_data, str):
                effective_stdin_data = stdin_data.encode(encoding, errors='replace')
            else:
                effective_stdin_data = stdin_data

        # Create the dataclass instance
        popen_build_args = PopenBuildArgs(
            command=command,
            directory=normalized_directory,
            encoding=encoding,
            envs=process_envs,
            stdin_data=effective_stdin_data,
        )

        command_to_execute, popen_kwargs = self._build_popen_args_and_options(popen_build_args)

        def _create_subprocess_sync():
            return subprocess.Popen(
                command_to_execute,
                **popen_kwargs
            )

        try:
            process = await asyncio.to_thread(_create_subprocess_sync)
        except FileNotFoundError:
            raise CommandExecutionError(f"Command not found: {original_command[0]!r}")
        except Exception as e:
            raise CommandExecutionError(f"Failed to start command {original_command!r}: {e}") from e

        if effective_stdin_data and process.stdin:
            def _write_stdin_sync():
                try:
                    if process.stdin:
                        stdin_writer: BinaryIO = cast(BinaryIO, process.stdin)
                        stdin_writer.write(effective_stdin_data)
                        stdin_writer.close()
                except (BrokenPipeError, ConnectionResetError) as e:
                    # Log the error but don't re-raise, as it might be due to process quick exit
                    logger.debug(f"Broken pipe or connection reset while writing to stdin: {e}")
                except Exception as e:
                    logger.warning(f"Error writing to stdin for process {process.pid}: {e}")
            await asyncio.to_thread(_write_stdin_sync)
            
        return process

    @abstractmethod
    def _build_popen_args_and_options(self, args: PopenBuildArgs) -> Tuple[List[str], Dict[str, Any]]:
        """
        抽象方法：根据参数构建 subprocess.Popen 的 args (命令列表) 和 options (关键字参数字典)。

        Args:
            args (PopenBuildArgs): 包含命令、工作目录、编码、环境变量和标准输入数据的dataclass。

        Returns:
            Tuple[List[str], Dict[str, Any]]:
                - List[str]: 实际要执行的命令列表（即Popen的args）。
                - Dict[str, Any]: Popen方法调用的关键字参数字典（例如cwd, stdin, stdout, stderr, env, creationflags）。
        """
        pass


class WindowsSubprocessProcessFactory(BaseSubprocessProcessFactory):
    """
    Factory for creating subprocess.Popen instances on Windows.
    """
    def support_platform(self) -> Optional[int]:
        return PriorityEnum.PLATFORM_LEVEL if sys.platform == "win32" else None

    def _build_popen_args_and_options(self, args: PopenBuildArgs) -> Tuple[List[str], Dict[str, Any]]:
        command = args.command
        directory = args.directory
        encoding = args.encoding
        envs = args.envs
        stdin_data = args.stdin_data

        windows_builtins = {
            'echo', 'dir', 'cd', 'copy', 'del', 'md', 'mkdir', 'rd', 'rmdir',
            'type', 'cls', 'date', 'time', 'vol', 'ver', 'set', 'path',
            'move', 'ren', 'rename', 'attrib', 'find', 'fc', 'comp'
        }

        command_to_execute: List[str]
        if command[0].lower() == 'echo':
            quoted_message = subprocess.list2cmdline(command[1:])
            command_to_execute = ["powershell.exe", "-Command", f"Write-Host {quoted_message}"]
        elif command[0].lower() in windows_builtins:
            command_to_execute = ["cmd.exe", "/c"] + command
        else:
            executable_path = shutil.which(command[0], path=envs.get('PATH'))

            if not executable_path:
                raise CommandExecutionError(f"Command not found: {command[0]!r}")

            # For all general executables (including Python scripts, Node.js, uv etc.)
            # let subprocess.Popen directly use CreateProcessW with the command list.
            # This ensures arguments with spaces, newlines, or special characters are
            # passed as single arguments to the target executable's argv correctly on Windows.
            command_to_execute = [executable_path] + command[1:]

        creationflags = subprocess.CREATE_NO_WINDOW

        stdin_mode = subprocess.PIPE if stdin_data else None
        if not stdin_data and len(command_to_execute) > 2 and command_to_execute[0].lower() == "cmd.exe":
            stdin_mode = subprocess.DEVNULL

        popen_kwargs = {
            "cwd": directory,
            "stdin": stdin_mode,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": envs,
            "creationflags": creationflags,
            "encoding": encoding if sys.version_info < (3, 10) else None, # For Python < 3.10
            "text": False # Always use binary pipes and decode manually
        }
        return command_to_execute, popen_kwargs


class UnixSubprocessProcessFactory(BaseSubprocessProcessFactory):
    """
    Factory for creating subprocess.Popen instances on Unix-like systems.
    """
    def support_platform(self) -> Optional[int]:
        return PriorityEnum.PLATFORM_LEVEL if sys.platform != "win32" else None

    def _build_popen_args_and_options(self, args: PopenBuildArgs) -> Tuple[List[str], Dict[str, Any]]:
        command = args.command
        directory = args.directory
        encoding = args.encoding
        envs = args.envs
        stdin_data = args.stdin_data

        # On Unix, use shutil.which to find the executable path
        executable_path = shutil.which(command[0], path=envs.get('PATH'))

        if not executable_path:
            raise CommandExecutionError(f"Command not found: {command[0]!r}")

        command_to_execute = [executable_path] + command[1:]

        popen_kwargs = {
            "cwd": directory,
            "stdin": subprocess.PIPE if stdin_data else None,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": envs,
            "text": False # Always use binary pipes and decode manually
        }
        return command_to_execute, popen_kwargs


factories = [
    WindowsSubprocessProcessFactory(),
    UnixSubprocessProcessFactory(),
]

async def create_process(
    command: List[str],
    directory: str,
    encoding: str,
    envs: Dict[str, str],
    stdin_data: Optional[bytes | str],
) -> subprocess.Popen:
    """
    Create a subprocess.Popen instance.
    创建 subprocess.Popen 实例。

    Args:
        command: 命令列表。
        directory: 工作目录。
        encoding: 编码。
        envs: 环境变量。
        stdin_data: 标准输入数据。

    Returns:
        subprocess.Popen: 创建的 subprocess.Popen 实例。

    Raises:
        CommandExecutionError: 命令执行错误。
    """
    # 遍历所有工厂，找到支持当前平台的工厂，并按照优先级排序，取优先级最高的工厂创建进程。数值越小，优先级越高。
    # Filter and sort factories based on their support for the current platform.
    # Using list comprehension for better readability and type inference.
    available_factories = [
        (f.support_platform(), f) for f in factories if f.support_platform() is not None
    ]
    available_factories.sort(key=lambda item: item[0]) # Sort by priority (item[0])

    if not available_factories:
        raise CommandExecutionError(f"No factory supports the current platform.")
    
    # Get the factory with the highest priority (lowest numerical value)
    selected_factory = available_factories[0][1]
    
    return await selected_factory.create_process(
        command=command,
        directory=directory,
        encoding=encoding,
        envs=envs,
        stdin_data=stdin_data,
    )

class SubprocessProcess(IProcess):
    """Subprocess-based implementation of IProcess."""
    
    def __init__(
        self,
        process: "subprocess.Popen",
        info: ProcessInfo,
        output_manager: IOutputManager,
        monitor_task: asyncio.Task,
    ):
        import subprocess
        self._process: subprocess.Popen = process
        self._info = info
        self._output_manager = output_manager
        self._completion_event: Optional[asyncio.Event] = None
        self._monitor_task = monitor_task
        self._is_stopping = False

    def _get_completion_event(self) -> asyncio.Event:
        """获取 completion event，如果不存在则在当前事件循环中创建新的"""
        if self._completion_event is None:
            try:
                self._completion_event = asyncio.Event()
            except RuntimeError:
                self._completion_event = asyncio.Event()
        return self._completion_event

    @property
    def pid(self) -> str:
        return self._info.pid

    async def get_details(self) -> ProcessInfo:
        return self._info

    async def wait_for_completion(self, timeout: Optional[int] = None) -> ProcessInfo:
        try:
            completion_event = self._get_completion_event()
            await asyncio.wait_for(completion_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise ProcessTimeoutError(f"Process {self.pid} timed out after {timeout} seconds.")
        return self._info

    async def get_output(
        self,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        async for entry in self._output_manager.get_output(
            self.pid, output_key, since, until, tail
        ):
            yield entry

    async def stop(self, force: bool = False, reason: Optional[str] = None) -> None:
        if self._info.status != ProcessStatus.RUNNING:
            return

        self._is_stopping = True
        if reason:
            self._info.error_message = reason
        
        try:
            def _terminate_process():
                """在线程中终止进程"""
                if force:
                    self._process.kill()
                else:
                    self._process.terminate()
            
            # 在线程池中执行进程终止
            await asyncio.to_thread(_terminate_process)
            
            # Wait for the monitor to confirm the process has exited
            try:
                completion_event = self._get_completion_event()
                await asyncio.wait_for(completion_event.wait(), timeout=15)
            except RuntimeError as e:
                if "different event loop" in str(e):
                    self._completion_event = None
                    completion_event = self._get_completion_event()
                    await asyncio.wait_for(completion_event.wait(), timeout=5)
                else:
                    raise
        except ProcessLookupError:
            pass
        except asyncio.TimeoutError:
            self._info.error_message = (self._info.error_message or "") + "Timed out waiting for termination."
        except Exception as e:
            if "different event loop" in str(e):
                self._info.error_message = (self._info.error_message or "") + "Event loop binding issue during stop."
            else:
                raise ProcessControlError(f"Failed to stop process {self.pid}: {e}") from e

    async def clean(self) -> str:
        if self._info.status == ProcessStatus.RUNNING:
            raise ProcessCleanError(f"Cannot clean running process {self.pid}. Stop it first.")
        
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            
        await self._output_manager.clear_output(self.pid)
        return "Success"




class SubprocessBaseProcessManager(IProcessManager):
    """Subprocess-based implementation of IProcessManager."""
    
    def __init__(self, output_manager: IOutputManager, process_retention_seconds: int = 3600):
        self._output_manager = output_manager
        self._process_retention_seconds = process_retention_seconds
        self._processes: Dict[str, SubprocessProcess] = {}
        self._cleanup_handles: Dict[str, asyncio.Handle] = {}
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    async def initialize(self) -> None:
        """
        Initialize the process manager and save the current event loop.
        """
        try:
            # Save the current event loop as the main loop
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            # If no event loop is running, this will be set later when needed
            self._main_loop = None

    async def shutdown(self) -> None:
        # Cancel all pending cleanup handles
        for handle in self._cleanup_handles.values():
            if not handle.cancelled():
                handle.cancel()
        self._cleanup_handles.clear()

        # Stop all running processes
        running_procs = [proc for proc in self._processes.values() if proc._info.status == ProcessStatus.RUNNING]
        if running_procs:
            await asyncio.gather(
                *(proc.stop(force=True, reason="Manager is shutting down") for proc in running_procs),
                return_exceptions=True
            )
        
        # Wait for all monitor tasks to complete
        all_monitor_tasks = [proc._monitor_task for proc in self._processes.values() if proc._monitor_task and not proc._monitor_task.done()]
        if all_monitor_tasks:
            await asyncio.gather(
                *all_monitor_tasks,
                return_exceptions=True
            )

    def _schedule_process_cleanup(self, process_id: str) -> None:
        """同步函数，用于call_later调用，创建异步清理任务"""
        if process_id in self._processes:
            # 使用主事件循环创建异步任务来执行清理
            if self._main_loop and not self._main_loop.is_closed():
                # 创建带名称的任务
                task = self._main_loop.create_task(
                    self._cleanup_single_process(process_id),
                    name=f"cleanup_process_{process_id}"
                )
                # 不需要保存 task 引用，因为它是自清理的
            else:
                # 如果主循环不可用，使用当前循环
                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(
                        self._cleanup_single_process(process_id),
                        name=f"cleanup_process_{process_id}"
                    )
                    # 不需要保存 task 引用，因为它是自清理的
                except RuntimeError:
                    # 没有运行的事件循环，尝试从全局获取一个
                    try:
                        # 获取当前线程的事件循环
                        loop = asyncio.get_event_loop()
                        if loop and not loop.is_closed():
                            task = loop.create_task(
                                self._cleanup_single_process(process_id),
                                name=f"cleanup_process_{process_id}"
                            )
                        else:
                            # 如果真的没有可用的事件循环，我们直接在线程池中执行清理
                            import threading
                            import os
                            def sync_cleanup():
                                # 这是一个同步的清理，仅作为最后的备用方案
                                try:
                                    # 直接从 _processes 中移除进程，跳过异步清理
                                    self._cleanup_handles.pop(process_id, None)
                                    if process_id in self._processes:
                                        del self._processes[process_id]
                                except Exception:
                                    # 静默处理清理错误
                                    pass
                            
                            # 在后台线程中执行同步清理
                            threading.Thread(target=sync_cleanup, daemon=True).start()
                    except Exception:
                        # 如果所有方法都失败，至少清理 cleanup handle
                        self._cleanup_handles.pop(process_id, None)

    async def _cleanup_single_process(self, process_id: str) -> None:
        """清理单个进程"""
        try:
            # 移除cleanup handle记录
            self._cleanup_handles.pop(process_id, None)
            
            if process_id not in self._processes:
                return
                
            process = self._processes[process_id]
            
            # 只清理非运行状态的进程（包括完成、失败、终止、错误状态）
            if process._info.status == ProcessStatus.RUNNING:
                logger.warning(f"Attempted to clean running process {process_id}. Skipping.")
                return

            await process.clean()
            del self._processes[process_id]
        except ProcessNotFoundError:
            logger.debug(f"Process {process_id} not found during cleanup, already removed.")
        except Exception as e:
            logger.error(f"Error cleaning up process {process_id}: {e}", exc_info=True)


    def _schedule_cleanup_for_process(self, process_id: str) -> None:
        if self._process_retention_seconds >= 0:
            if self._main_loop and not self._main_loop.is_closed():
                # Schedule the asynchronous cleanup task in the main event loop
                handle = self._main_loop.call_later(
                    self._process_retention_seconds,
                    self._schedule_process_cleanup,
                    process_id
                )
                self._cleanup_handles[process_id] = handle
            else:
                logger.warning(f"Cannot schedule cleanup for process {process_id}: main event loop is not available or closed.")

    async def start_process(
        self,
        command: List[str],
        directory: str,
        description: str,
        stdin_data: Optional[bytes | str] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> IProcess:
        
        # Generate a unique 5-character random PID
        process_id = self._generate_unique_pid()
        start_time = datetime.now(timezone.utc)
        
        # Prepare environment
        process_envs = os.environ.copy()
        if envs:
            process_envs.update(envs)

        # Store the original command for display and later use
        original_command = command.copy()
        
        # Use the factory to create the subprocess
        try:
            process = await create_process(
                command=command,
                directory=directory,
                encoding=encoding if encoding is not None else (sys.getdefaultencoding() or 'utf-8'),
                envs=process_envs,
                stdin_data=stdin_data,
            )
        except Exception as e:
            raise CommandExecutionError(f"Failed to start command '{' '.join(original_command)}': {e}") from e

        info = ProcessInfo(
            pid=process_id,
            command=original_command,
            directory=directory,
            description=description,
            status=ProcessStatus.RUNNING,
            start_time=start_time,
            end_time=None,
            exit_code=None,
            error_message=None,
            timeout=timeout,
            labels=labels or [],
            envs=process_envs, # Add the missing envs field
        )

        # Use default values for timeout and encoding
        effective_timeout = timeout if timeout is not None else sys.maxsize
        effective_encoding = encoding if encoding is not None else (sys.getdefaultencoding() or 'utf-8')

        # Create monitor task
        if self._main_loop and not self._main_loop.is_closed():
            monitor_task = self._main_loop.create_task(
                self._monitor_subprocess(process_id, process, effective_timeout, effective_encoding),
                name=f"monitor_subprocess_{process_id}"
            )
        else:
            monitor_task = asyncio.create_task(
                self._monitor_subprocess(process_id, process, effective_timeout, effective_encoding),
                name=f"monitor_subprocess_{process_id}"
            )
        
        process_obj = SubprocessProcess(process, info, self._output_manager, monitor_task)
        self._processes[process_id] = process_obj
        
        # Store a message to ensure log directory is created
        await self._output_manager.store_output(process_id, "manager", f"Process created at {start_time}")
        
        return process_obj

    def _generate_unique_pid(self) -> str:
        """Generates a unique 5-character random PID."""
        retry_count = 0
        while True:
            pid = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            if pid not in self._processes:
                return pid
            else:
                retry_count += 1
                if retry_count >= 10:
                    raise Exception("Failed to generate a unique PID after 10 retries.")

    async def _monitor_subprocess(self, process_id: str, process: "subprocess.Popen", timeout: int, encoding: str):
        """Monitor a subprocess and handle its output and completion."""
        proc_obj = self._processes[process_id]
        info = proc_obj._info
        output_encoding = encoding

        async def read_stream_in_thread(stream, output_key):
            def _read_one_line_sync():
                try:
                    return stream.readline()  # This is a blocking read
                except Exception as e:
                    logger.debug(f"Error reading from {output_key} stream: {e}")
                    return None

            while True:
                try:
                    line_bytes = await asyncio.to_thread(_read_one_line_sync)
                    if line_bytes is None:  # Error occurred during read
                        break
                    if not line_bytes:  # EOF reached
                        break
                    
                    line = line_bytes.decode(output_encoding, errors='replace').rstrip('\r\n')
                    if line:  # Only store non-empty lines
                        await self._output_manager.store_output(process_id, output_key, line)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    error_msg = f"Error in {output_key} stream monitor: {e}"
                    await self._output_manager.store_output(process_id, "stderr", error_msg)
                    break

        stdout_task = asyncio.create_task(
            read_stream_in_thread(process.stdout, "stdout"),
            name=f"read_stdout_{process_id}"
        )
        stderr_task = asyncio.create_task(
            read_stream_in_thread(process.stderr, "stderr"),
            name=f"read_stderr_{process_id}"
        )

        try:
            def _wait_for_process_sync():
                return process.wait() # This will immediately return if the process has already exited
            
            wait_task = asyncio.create_task(
                asyncio.to_thread(_wait_for_process_sync),
                name=f"wait_subprocess_{process_id}"
            )
            
            exit_code = await asyncio.wait_for(wait_task, timeout=timeout)
            info.exit_code = exit_code
            
            if proc_obj._is_stopping:
                info.status = ProcessStatus.TERMINATED
            else:
                info.status = ProcessStatus.COMPLETED if exit_code == 0 else ProcessStatus.FAILED
            
            # Process exited, wait for output reading tasks to complete for a short while
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=5  # Give 5 seconds for output reading to finish
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                await self._output_manager.store_output(process_id, "stderr", "Warning: Output reading timed out after process exit")
                
        except asyncio.TimeoutError:
            info.status = ProcessStatus.TERMINATED
            info.error_message = f"Process timed out after {timeout} seconds and was terminated."
            
            def _force_kill_sync():
                try:
                    process.kill()
                    process.wait()
                except:
                    pass
            
            await asyncio.to_thread(_force_kill_sync)
            info.exit_code = process.returncode
            
            # Try to get any remaining output after termination
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=5  # Give 5 seconds for output reading after termination
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                await self._output_manager.store_output(process_id, "stderr", "Warning: Output reading interrupted due to timeout during termination")
            
        except Exception as e:
            info.status = ProcessStatus.ERROR
            info.error_message = str(e)
            info.exit_code = process.returncode
            
            # Wait for output reading tasks to complete in case of error
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=5  # Give 5 seconds for output reading after error
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                await self._output_manager.store_output(process_id, "stderr", f"Warning: Output reading interrupted due to error: {e}")
        finally:
            info.end_time = datetime.now(timezone.utc)
            
            # Ensure output reading tasks are completed or cancelled
            if not stdout_task.done():
                stdout_task.cancel()
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

            # Close the process streams (important for releasing resources)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            if process.stdin: # Should already be closed by factory, but for robustness
                process.stdin.close()

            # Set completion event
            completion_event = proc_obj._get_completion_event()
            completion_event.set()
            
            # Schedule cleanup
            self._schedule_cleanup_for_process(process_id)

    async def stop_process(self, process_id: str, force: bool = False, reason: Optional[str] = None) -> None:
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")
        await self._processes[process_id].stop(force=force, reason=reason)

    async def get_process(self, process_id: str) -> IProcess:
        await self._cleanup_expired_processes()
        
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")
        return self._processes[process_id]

    async def get_process_info(self, process_id: str) -> ProcessInfo:
        await self._cleanup_expired_processes()
        
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")
        return await self._processes[process_id].get_details()

    async def list_processes(
        self, status: Optional[ProcessStatus] = None, labels: Optional[List[str]] = None
    ) -> List[ProcessInfo]:
        await self._cleanup_expired_processes()
        
        procs = list(self._processes.values())

        if status:
            procs = [p for p in procs if p._info.status == status]
        if labels:
            procs = [p for p in procs if all(label in p._info.labels for label in labels)]
        return [p._info for p in procs]

    async def _cleanup_expired_processes(self) -> None:
        expired_pids = []
        for pid, proc_obj in self._processes.items():
            if proc_obj._info.status in [ProcessStatus.COMPLETED, ProcessStatus.FAILED, ProcessStatus.TERMINATED, ProcessStatus.ERROR]:
                if self._process_retention_seconds >= 0:
                    # Check if the process has expired based on retention time
                    if proc_obj._info.end_time:
                        elapsed_time = (datetime.now(timezone.utc) - proc_obj._info.end_time).total_seconds()
                        if elapsed_time > self._process_retention_seconds:
                            expired_pids.append(pid)
                # If retention_seconds is -1, means forever, so no auto-cleanup
        
        if expired_pids:
            logger.info(f"Auto-cleaning {len(expired_pids)} expired processes: {expired_pids}")
            await self.clean_processes(expired_pids)

    async def clean_processes(self, process_ids: List[str]) -> Dict[str, str]:
        results: Dict[str, str] = {}
        for pid in process_ids:
            try:
                proc_obj = self._processes.get(pid)
                if not proc_obj:
                    results[pid] = "Process not found."
                    continue
                
                # Check if the process is still running
                if proc_obj._info.status == ProcessStatus.RUNNING:
                    results[pid] = "Cannot clean a running process. Stop it first."
                    continue

                await proc_obj.clean()
                del self._processes[pid]
                if pid in self._cleanup_handles:
                    self._cleanup_handles[pid].cancel()
                    del self._cleanup_handles[pid]
                results[pid] = "Cleaned successfully."
            except Exception as e:
                results[pid] = f"Failed to clean: {e}"
                logger.error(f"Error cleaning process {pid}: {e}", exc_info=True)
        return results 
    
