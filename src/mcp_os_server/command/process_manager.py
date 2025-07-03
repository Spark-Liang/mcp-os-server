from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
import shutil
import random
import string
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, List, Optional

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


class Process(IProcess):
    _info: ProcessInfo
    _process: asyncio.subprocess.Process
    _output_manager: IOutputManager
    _completion_event: Optional[asyncio.Event]
    _monitor_task: asyncio.Task
    _is_stopping: bool

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        info: ProcessInfo,
        output_manager: IOutputManager,
        monitor_task: asyncio.Task,
    ):
        self._process = process
        self._info = info
        self._output_manager = output_manager
        self._completion_event = None  # 延迟创建，避免事件循环绑定问题
        self._monitor_task = monitor_task
        self._is_stopping = False

    def _get_completion_event(self) -> asyncio.Event:
        """获取 completion event，如果不存在则在当前事件循环中创建新的"""
        if self._completion_event is None:
            try:
                self._completion_event = asyncio.Event()
            except RuntimeError:
                # 如果没有运行的事件循环，创建一个新的 Event
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
            if force:
                self._process.kill()
            else:
                self._process.terminate()
            
            # Wait for the monitor to confirm the process has exited.
            # Add a timeout to prevent hanging indefinitely.
            try:
                completion_event = self._get_completion_event()
                await asyncio.wait_for(completion_event.wait(), timeout=15)
            except RuntimeError as e:
                if "different event loop" in str(e):
                    # 如果遇到事件循环绑定问题，重新创建 Event 并重试
                    self._completion_event = None
                    completion_event = self._get_completion_event()
                    await asyncio.wait_for(completion_event.wait(), timeout=5)
                else:
                    raise
        except ProcessLookupError:
            # Process already finished, which is fine.
            pass
        except asyncio.TimeoutError:
            # If it times out, the caller can decide if that's an error.
            # Forcing a kill again might be an option, but for now we just log it.
            self._info.error_message = (self._info.error_message or "") + "Timed out waiting for termination."
        except Exception as e:
            # 捕获所有其他异常，包括事件循环相关的错误
            if "different event loop" in str(e):
                # 对于事件循环错误，我们简单地记录但不抛出异常
                self._info.error_message = (self._info.error_message or "") + "Event loop binding issue during stop."
            else:
                raise ProcessControlError(f"Failed to stop process {self.pid}: {e}") from e

    async def clean(self) -> str:
        if self._info.status == ProcessStatus.RUNNING:
            raise ProcessCleanError(f"Cannot clean running process {self.pid}. Stop it first.")
        
        # Cancel the monitor task if it's somehow still running
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            
        await self._output_manager.clear_output(self.pid)
        return "Success"


class ProcessManager(IProcessManager):
    def __init__(self, output_manager: IOutputManager, process_retention_seconds: int = 3600):
        self._output_manager = output_manager
        self._process_retention_seconds = process_retention_seconds
        self._processes: Dict[str, Process] = {}
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
            else:
                # 如果主循环不可用，使用当前循环
                task = asyncio.create_task(
                    self._cleanup_single_process(process_id),
                    name=f"cleanup_process_{process_id}"
                )

    async def _cleanup_single_process(self, process_id: str) -> None:
        """清理单个进程"""
        try:
            # 移除cleanup handle记录
            self._cleanup_handles.pop(process_id, None)
            
            if process_id not in self._processes:
                return
                
            process = self._processes[process_id]
            
            # 只清理非运行状态的进程
            if process._info.status not in [ProcessStatus.RUNNING, ProcessStatus.TERMINATED]:
                await self.clean_processes([process_id])
        except Exception:
            # 静默处理清理错误，避免影响其他进程
            pass

    def _schedule_cleanup_for_process(self, process_id: str) -> None:
        """为进程安排延迟清理任务"""
        # 取消之前的清理任务（如果存在）
        if process_id in self._cleanup_handles:
            old_handle = self._cleanup_handles[process_id]
            if not old_handle.cancelled():
                old_handle.cancel()
        
        # 安排新的清理任务
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            self._process_retention_seconds,
            self._schedule_process_cleanup,
            process_id
        )
        self._cleanup_handles[process_id] = handle

    async def start_process(
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
        # Normalize and validate the directory path
        normalized_directory = os.path.abspath(directory)
        if not os.path.isdir(normalized_directory):
            raise CommandExecutionError(f"Directory not found: {directory}")

        # Generate a unique 5-character random PID
        process_id = self._generate_unique_pid()
        start_time = datetime.now(timezone.utc)
        
        # Prepare environment
        process_envs = os.environ.copy()
        if envs:
            process_envs.update(envs)

        # Store the original command for display and later use
        original_command = command.copy()
        
        # On Windows, check for built-in commands first to avoid conflicts with external tools
        if sys.platform == "win32":
            # List of common Windows built-in commands
            windows_builtins = {
                'echo', 'dir', 'cd', 'copy', 'del', 'md', 'mkdir', 'rd', 'rmdir', 
                'type', 'cls', 'date', 'time', 'vol', 'ver', 'set', 'path',
                'move', 'ren', 'rename', 'attrib', 'find', 'fc', 'comp'
            }
            
            if command[0].lower() in windows_builtins:
                # Use cmd.exe to run built-in commands on Windows
                command_to_execute = ["cmd.exe", "/c"] + command
            else:
                # Determine the executable path using shutil.which for non-builtin commands
                # Pass the process_envs to shutil.which to respect custom PATH
                executable_path = shutil.which(command[0], path=process_envs.get('PATH'))
                
                if not executable_path:
                    raise CommandExecutionError(f"Command not found: {original_command[0]}")
                else:
                    # Check if the found executable is a script file that needs cmd.exe to run
                    executable_path_lower = executable_path.lower()
                    if executable_path_lower.endswith(('.cmd', '.bat', '.com')):
                        # Use cmd.exe to run script files on Windows
                        command_to_execute = ["cmd.exe", "/c", executable_path] + command[1:]
                    else:
                        # Use the full path found by shutil.which, and keep the rest of the arguments
                        command_to_execute = [executable_path] + command[1:]
        else:
            # Non-Windows systems: use shutil.which to find commands
            executable_path = shutil.which(command[0], path=process_envs.get('PATH'))
            
            if not executable_path:
                raise CommandExecutionError(f"Command not found: {original_command[0]}")
            else:
                # Use the full path found by shutil.which, and keep the rest of the arguments
                command_to_execute = [executable_path] + command[1:]

        try:
            # For CMD scripts on Windows, ensure proper stdin handling
            stdin_mode = None
            if stdin_data:
                stdin_mode = asyncio.subprocess.PIPE
            elif sys.platform == "win32" and len(command_to_execute) > 2 and command_to_execute[0].lower() == "cmd.exe":
                # For cmd.exe executed scripts, explicitly close stdin to prevent hanging
                stdin_mode = asyncio.subprocess.DEVNULL
            else:
                stdin_mode = None
                
            # 使用主事件循环创建子进程，如果主循环不可用则使用默认方法
            async def _create_subprocess():
                """带名称的子进程创建函数"""
                return await asyncio.create_subprocess_exec(
                    *command_to_execute,
                    cwd=directory,
                    stdin=stdin_mode,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=process_envs,
                )
            
            if self._main_loop and not self._main_loop.is_closed():
                # 临时设置事件循环以确保 create_subprocess_exec 使用正确的循环
                original_loop = None
                try:
                    original_loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                
                if original_loop != self._main_loop:
                    # 如果当前循环不是主循环，需要在主循环中执行
                    subprocess_task = self._main_loop.create_task(
                        _create_subprocess(),
                        name=f"create_subprocess_{process_id}"
                    )
                    future = asyncio.run_coroutine_threadsafe(subprocess_task, self._main_loop)
                    process = await asyncio.wrap_future(future)
                else:
                    # 当前就在主循环中，创建带名称的任务
                    subprocess_task = self._main_loop.create_task(
                        _create_subprocess(),
                        name=f"create_subprocess_{process_id}"
                    )
                    process = await subprocess_task
            else:
                # 主循环不可用，使用默认方法但仍创建带名称的任务
                subprocess_task = asyncio.create_task(
                    _create_subprocess(),
                    name=f"create_subprocess_{process_id}"
                )
                process = await subprocess_task
        except FileNotFoundError:
            raise CommandExecutionError(f"Command not found: {original_command[0]}")
        except Exception as e:
            raise CommandExecutionError(f"Failed to start command '{' '.join(original_command)}': {e}") from e

        if stdin_data and process.stdin:
            try:
                process.stdin.write(stdin_data)
                await process.stdin.drain()
                process.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                # This can happen if the process exits quickly before stdin is fully written.
                pass


        info = ProcessInfo(
            pid=process_id,
            command=original_command,  # Store the original command for display
            directory=directory,
            description=description,
            status=ProcessStatus.RUNNING,
            start_time=start_time,
            end_time=None,
            exit_code=None,
            error_message=None,
            timeout=timeout,
            labels=labels or [],
        )

        # 使用主事件循环创建带名称的监控任务
        if self._main_loop and not self._main_loop.is_closed():
            monitor_task = self._main_loop.create_task(
                self._monitor_process(process_id, process, timeout, encoding),
                name=f"monitor_process_{process_id}"
            )
        else:
            monitor_task = asyncio.create_task(
                self._monitor_process(process_id, process, timeout, encoding),
                name=f"monitor_process_{process_id}"
            )
        
        process_obj = Process(process, info, self._output_manager, monitor_task)
        self._processes[process_id] = process_obj
        
        # Store a message to ensure log directory is created
        await self._output_manager.store_output(process_id, "manager", f"Process created at {start_time}")
        
        return process_obj

    def _generate_unique_pid(self) -> str:
        """Generates a unique 5-character random PID."""
        retry_count = 0
        while True:
            # Generate a 5-character random string (letters and digits)
            pid = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            if pid not in self._processes:
                return pid
            else:
                retry_count += 1
                if retry_count >= 10:
                    raise Exception("Failed to generate a unique PID after 10 retries.")

    async def _monitor_process(self, process_id: str, process: asyncio.subprocess.Process, timeout: Optional[int], encoding: Optional[str]):
        proc_obj = self._processes[process_id]
        info = proc_obj._info
        
        # Default to system's encoding or fallback to utf-8
        output_encoding = encoding or sys.getdefaultencoding() or 'utf-8'

        async def read_stream(stream: Optional[asyncio.StreamReader], output_key: str):
            if not stream:
                return
            while True:
                try:
                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode(output_encoding, errors='replace').rstrip('\r\n')
                    if line:  # 只存储非空行
                        await self._output_manager.store_output(process_id, output_key, line)
                except asyncio.CancelledError:
                    # 如果任务被取消，正常退出
                    break
                except UnicodeDecodeError as e:
                    # 编码错误，记录错误但继续
                    error_msg = f"Encoding error in {output_key}: {e}"
                    await self._output_manager.store_output(process_id, "stderr", error_msg)
                    break
                except Exception as e:
                    # 其他异常，记录错误
                    error_msg = f"Error reading {output_key}: {e}"
                    await self._output_manager.store_output(process_id, "stderr", error_msg)
                    break

        stdout_task = asyncio.create_task(
            read_stream(process.stdout, "stdout"),
            name=f"read_stdout_{process_id}"
        )
        stderr_task = asyncio.create_task(
            read_stream(process.stderr, "stderr"),
            name=f"read_stderr_{process_id}"
        )

        try:
            # 为 process.wait() 创建带名称的任务
            wait_task = asyncio.create_task(process.wait(), name=f"wait_process_{process_id}")
            exit_code = await asyncio.wait_for(wait_task, timeout=timeout)
            info.exit_code = exit_code
            if proc_obj._is_stopping:
                info.status = ProcessStatus.TERMINATED
            else:
                info.status = ProcessStatus.COMPLETED if exit_code == 0 else ProcessStatus.FAILED
            
            # 进程已退出，等待输出读取任务完成
            # 给输出读取任务足够的时间来完成剩余的读取
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=10  # 增加到10秒，确保输出完全读取
                )
            except asyncio.TimeoutError:
                # 如果输出读取超时，则取消任务
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                await self._output_manager.store_output(process_id, "stderr", "Warning: Output reading timed out")
                
        except asyncio.TimeoutError:
            info.status = ProcessStatus.TERMINATED
            info.error_message = f"Process timed out after {timeout} seconds and was terminated."
            await proc_obj.stop(force=True) # Ensure it's killed
            info.exit_code = process.returncode
            
            # 超时情况下给输出读取更多时间
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=5  # 给5秒时间完成输出读取
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                await self._output_manager.store_output(process_id, "stderr", "Warning: Output reading interrupted due to timeout")
            
        except Exception as e:
            info.status = ProcessStatus.ERROR
            info.error_message = str(e)
            info.exit_code = process.returncode
            
            # 异常情况下也需要等待输出读取完成
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=5  # 异常情况下给5秒时间
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                await self._output_manager.store_output(process_id, "stderr", f"Warning: Output reading interrupted due to error: {e}")
        finally:
            info.end_time = datetime.now(timezone.utc)
            
            # 确保输出读取任务完成后才设置 completion event
            # 这是关键修复：避免竞争条件
            if not stdout_task.done() or not stderr_task.done():
                # 如果任务还未完成，等待它们或取消
                try:
                    await asyncio.wait_for(
                        asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                        timeout=2  # 最后的2秒超时
                    )
                except asyncio.TimeoutError:
                    stdout_task.cancel()
                    stderr_task.cancel()
                    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            
            # 现在安全地设置 completion event
            completion_event = proc_obj._get_completion_event()
            completion_event.set()
            
            # 进程结束后，安排延迟清理任务
            self._schedule_cleanup_for_process(process_id)

    async def stop_process(self, process_id: str, force: bool = False, reason: Optional[str] = None) -> None:
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")
        await self._processes[process_id].stop(force=force, reason=reason)

    async def get_process(self, process_id: str) -> IProcess:
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")
        return self._processes[process_id]

    async def get_process_info(self, process_id: str) -> ProcessInfo:
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process with ID {process_id} not found.")
        return await self._processes[process_id].get_details()

    async def list_processes(
        self, status: Optional[ProcessStatus] = None, labels: Optional[List[str]] = None
    ) -> List[ProcessInfo]:
        procs = list(self._processes.values())

        if status:
            procs = [p for p in procs if p._info.status == status]

        if labels:
            procs = [p for p in procs if set(labels).issubset(set(p._info.labels))]

        return [p._info for p in procs]

    async def clean_processes(self, process_ids: List[str]) -> Dict[str, str]:
        results = {}
        for process_id in process_ids:
            if process_id in self._processes:
                # 取消对应的清理任务
                if process_id in self._cleanup_handles:
                    handle = self._cleanup_handles[process_id]
                    if not handle.cancelled():
                        handle.cancel()
                    del self._cleanup_handles[process_id]
                
                process = self._processes[process_id]
                try:
                    await process.clean()
                    results[process_id] = "Success"
                    del self._processes[process_id]
                except ProcessCleanError as e:
                    results[process_id] = f"Failed: {e}"
            else:
                results[process_id] = "Not found"
        return results 