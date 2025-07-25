from __future__ import annotations
import asyncio
import os
import sys
import uuid
import time
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, AsyncGenerator, cast

import anyio
from anyio.abc import Process as AnyioProcessABC, ByteReceiveStream, Task

from .interfaces import IProcess, IProcessManager, IOutputManager
from .models import (
    OutputMessageEntry,
    ProcessInfo,
    ProcessStatus,
)
from .exceptions import (
    ProcessNotFoundError,
    ProcessControlError,
    ProcessInfoRetrievalError,
    OutputRetrievalError,
    ProcessCleanError,
    CommandExecutionError,
)

class AnyioProcess(IProcess):
    """
    Implementation of IProcess using anyio.
    """
    def __init__(
        self,
        process_id: str,
        command: List[str],
        directory: str,
        description: str,
        start_time: float,
        output_manager: IOutputManager,
        stdin_data: Optional[bytes] = None,
        timeout: Optional[int] = None,
        envs: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ):
        self._id = process_id
        self._command = command
        self._directory = directory
        self._description = description
        self._start_time = start_time
        self._output_manager = output_manager
        self._stdin_data = stdin_data
        self._timeout = timeout
        self._envs = envs
        self._encoding = encoding if encoding else "utf-8"
        self._labels = labels if labels is not None else []

        self._anyio_process: Optional[AnyioProcessABC] = None
        self._status = ProcessStatus.RUNNING
        self._exit_code: Optional[int] = None
        self._end_time: Optional[float] = None
        self._output_tasks: List[Task] = [] 
        self._lock = anyio.Lock() # For thread-safe status updates

    @property
    def pid(self) -> str:
        return self._id

    async def _read_stream(self, stream_type: str, stream: ByteReceiveStream):
        """Helper to read from stdout/stderr and store in output manager."""
        buffer = bytearray()
        newline_bytes = self._encoding.encode('\n')
        try:
            async for chunk in stream:
                buffer.extend(chunk)
                while newline_bytes in buffer:
                    line_end_index = buffer.find(newline_bytes)
                    line_bytes = buffer[:line_end_index]
                    line = line_bytes.decode(self._encoding, errors='replace')
                    await self._output_manager.store_output(self._id, stream_type, line.rstrip('\r')) # rstrip \r just in case
                    del buffer[:line_end_index + len(newline_bytes)]
            
            # After loop, if there's any remaining data in buffer (no trailing newline)
            if buffer:
                line = buffer.decode(self._encoding, errors='replace')
                await self._output_manager.store_output(self._id, stream_type, line.rstrip('\r'))

        except anyio.EndOfStream:
            pass
        except Exception as e:
            # Log this error without relying on a logger, as this is low-level
            sys.stderr.write(f"Error reading {stream_type} for process {self._id}: {e}\n")

    async def _monitor_process(self):
        """Monitors the process for completion and updates its status."""
        # Ensure _anyio_process is not None before proceeding
        if self._anyio_process is None:
            sys.stderr.write(f"Attempted to monitor a process with no anyio_process object: {self._id}\n")
            return
        
        try:
            await self._anyio_process.wait()
            async with self._lock:
                self._exit_code = self._anyio_process.returncode
                self._end_time = time.time()
                self._status = ProcessStatus.COMPLETED if self._exit_code == 0 else ProcessStatus.FAILED
        except anyio.BrokenResourceError:
            # Process might have been stopped externally
            async with self._lock:
                if self._status == ProcessStatus.RUNNING: # Only change if not explicitly stopped
                    self._status = ProcessStatus.TERMINATED
                    self._end_time = time.time()
        except Exception as e:
            sys.stderr.write(f"Error monitoring process {self._id}: {e}\n")
            async with self._lock:
                self._status = ProcessStatus.FAILED
                self._end_time = time.time()
        finally:
            # Ensure output tasks are cancelled if process monitoring finishes
            for task in self._output_tasks:
                task.cancel()


    async def get_details(self) -> ProcessInfo:
        async with self._lock:
            if self._anyio_process is None:
                raise ProcessNotFoundError(f"Process {self._id} not found or not started.")
            
            # Update current status for running processes
            if self._status == ProcessStatus.RUNNING:
                # In Anyio, pid is only available once the process has started
                # and doesn't change. returncode is only available after wait()
                pass 

            return ProcessInfo(
                pid=self._id, # This is the unique identifier for the process instance
                command=self._command,
                directory=self._directory,
                description=self._description,
                status=self._status,
                start_time=datetime.fromtimestamp(self._start_time), # Convert float to datetime
                end_time=datetime.fromtimestamp(self._end_time) if self._end_time else None, # Convert float to datetime
                exit_code=self._exit_code,
                labels=self._labels,
                envs=cast(Dict[str, str], self._envs if self._envs is not None else {}), # Ensure envs is a Dict
                timeout=self._timeout,
                error_message=None # No error message tracking yet
            )

    async def wait_for_completion(self, timeout: Optional[int] = None) -> ProcessInfo:
        if self._anyio_process is None:
            raise ProcessNotFoundError(f"Process {self._id} not found or not started.")

        try:
            with anyio.fail_after(timeout) if timeout else anyio.CancelScope():
                await self._anyio_process.wait()
            
            # Status and exit code will be updated by _monitor_process
            return await self.get_details()
        except TimeoutError:
            await self.stop(force=True, reason=f"Process timed out after {timeout} seconds.")
            raise ProcessControlError(f"Process {self._id} timed out after {timeout} seconds.")
        except anyio.BrokenResourceError:
            # Process was stopped externally, or already completed/failed
            return await self.get_details()
        except Exception as e:
            raise ProcessControlError(f"Error waiting for process {self._id} completion: {e}")

    async def get_output(self,
                         output_key: str,
                         since: Optional[float] = None,
                         until: Optional[float] = None,
                         tail: Optional[int] = None) -> AsyncGenerator[OutputMessageEntry, None]:
        if output_key not in ["stdout", "stderr"]:
            raise ValueError(f"Invalid output_key: {output_key}. Must be 'stdout' or 'stderr'.")
        
        async for entry in self._output_manager.get_output(self._id, output_key, since, until, tail):
            yield entry

    async def stop(self, force: bool = False, reason: Optional[str] = None) -> None:
        async with self._lock:
            if self._anyio_process is None:
                raise ProcessNotFoundError(f"Process {self._id} not found or not started.")
            if self._status in [ProcessStatus.COMPLETED, ProcessStatus.FAILED, ProcessStatus.TERMINATED]:
                return # Already stopped or completed

            try:
                if force:
                    self._anyio_process.kill()
                    self._status = ProcessStatus.TERMINATED
                else:
                    self._anyio_process.terminate()
                    # Wait a bit for graceful shutdown, then kill if needed
                    try:
                        with anyio.fail_after(5): # Give 5 seconds for graceful exit
                            await self._anyio_process.wait()
                    except TimeoutError:
                        self._anyio_process.kill()
                        self._status = ProcessStatus.TERMINATED
                
                # Update status if process was terminated gracefully
                if self._status == ProcessStatus.RUNNING:
                    self._status = ProcessStatus.TERMINATED
                self._end_time = time.time()

            except anyio.BrokenResourceError:
                # Process already dead or resource cleaned up
                async with self._lock:
                    if self._status == ProcessStatus.RUNNING:
                        self._status = ProcessStatus.TERMINATED
                        self._end_time = time.time()
            except Exception as e:
                raise ProcessControlError(f"Error stopping process {self._id}: {e}")
            finally:
                for task in self._output_tasks:
                    task.cancel()


    async def clean(self) -> str:
        async with self._lock:
            if self._status == ProcessStatus.RUNNING:
                raise ProcessCleanError(f"Cannot clean running process {self._id}. Please stop it first.")
            try:
                # Ensure the anyio process resource is cleaned if still held
                if self._anyio_process and not self._anyio_process.returncode is None:
                    # There's no explicit close for anyio.abc.Process, it's managed by context
                    # If it's still running, it must be killed before cleaning output
                    if self._status == ProcessStatus.RUNNING:
                        self._anyio_process.kill() 
                
                await self._output_manager.clear_output(self._id)
                return f"Process {self._id} cleaned successfully."
            except ProcessNotFoundError:
                return f"Process {self._id} already cleaned or never existed."
            except Exception as e:
                raise ProcessCleanError(f"Error cleaning process {self._id}: {e}")


class AnyioProcessManager(IProcessManager):
    """
    Implementation of IProcessManager using anyio.
    """
    def __init__(self, output_manager: IOutputManager):
        self._output_manager = output_manager
        self._processes: Dict[str, AnyioProcess] = {}
        self._lock = anyio.Lock() # Protects access to _processes dictionary

    async def initialize(self) -> None:
        # No specific initialization needed for AnyioProcessManager itself
        # OutputManager should be initialized by the caller
        pass

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
        if not command or not directory:
            raise ValueError("Command and directory cannot be empty.")
        if not os.path.isdir(directory):
            raise ValueError(f"Directory '{directory}' does not exist.")

        process_id = str(uuid.uuid4())
        start_time = time.time()

        if encoding is None:
            encoding = "utf-8"

        stdin_bytes: Optional[bytes] = None
        if isinstance(stdin_data, str):
            stdin_bytes = stdin_data.encode(encoding)
        elif isinstance(stdin_data, bytes):
            stdin_bytes = stdin_data
        
        # Prepare environment variables
        full_envs = os.environ.copy()
        if envs:
            full_envs.update(envs)

        try:
            # Create a new AnyioProcess instance
            anyio_process_instance = AnyioProcess(
                process_id=process_id,
                command=command,
                directory=directory,
                description=description,
                start_time=start_time,
                output_manager=self._output_manager,
                stdin_data=stdin_bytes,
                timeout=timeout,
                envs=full_envs, # Pass combined environment variables
                encoding=encoding,
                labels=labels,
            )

            # Start the actual anyio process
            process = await anyio.open_process(
                command,
                cwd=directory,
                stdin=subprocess.PIPE, # Always use PIPE for stdin to allow writing data
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_envs,
            )
            anyio_process_instance._anyio_process = process

            if stdin_bytes and process.stdin:
                try:
                    await process.stdin.send(stdin_bytes)
                    await process.stdin.aclose()
                except anyio.BrokenResourceError:
                    # Process might have exited before stdin was fully written
                    pass
                except Exception as e:
                    sys.stderr.write(f"Error writing stdin to process {process_id}: {e}\n")

            async with anyio.create_task_group() as tg:
                # Start tasks to read stdout and stderr
                anyio_process_instance._output_tasks.append(tg.start_soon(anyio_process_instance._read_stream, "stdout", cast(ByteReceiveStream, process.stdout)))
                anyio_process_instance._output_tasks.append(tg.start_soon(anyio_process_instance._read_stream, "stderr", cast(ByteReceiveStream, process.stderr)))
                # Start task to monitor process completion
                anyio_process_instance._output_tasks.append(tg.start_soon(anyio_process_instance._monitor_process))
            
            async with self._lock:
                self._processes[process_id] = anyio_process_instance
            
            return anyio_process_instance

        except FileNotFoundError as e:
            raise CommandExecutionError(f"Command '{command[0]}' not found: {e}")
        except PermissionError as e:
            raise PermissionError(f"Permission denied to execute command '{command[0]}' or access directory '{directory}': {e}")
        except Exception as e:
            raise CommandExecutionError(f"Failed to start process for command '{' '.join(command)}': {e}")

    async def stop_process(self, process_id: str, force: bool = False, reason: Optional[str] = None) -> None:
        async with self._lock:
            process = self._processes.get(process_id)
            if not process:
                raise ProcessNotFoundError(f"Process {process_id} not found.")
        await process.stop(force=force, reason=reason)

    async def get_process_info(self, process_id: str) -> ProcessInfo:
        async with self._lock:
            process = self._processes.get(process_id)
            if not process:
                raise ProcessNotFoundError(f"Process {process_id} not found.")
        return await process.get_details()

    async def list_processes(self,
                             status: Optional[ProcessStatus] = None,
                             labels: Optional[List[str]] = None) -> List[ProcessInfo]:
        processes_info: List[ProcessInfo] = []
        async with self._lock:
            for proc_id in list(self._processes.keys()): # Iterate over a copy to allow modification during iteration
                try:
                    process = self._processes[proc_id]
                    p_info = await process.get_details()
                    
                    if status is not None and p_info.status != status:
                        continue
                    
                    if labels is not None:
                        if not all(label in p_info.labels for label in labels):
                            continue
                    
                    processes_info.append(p_info)

                except ProcessNotFoundError:
                    # If process is not found, it means it has been cleaned up. Remove it from tracking
                    del self._processes[proc_id]
                except Exception as e:
                    sys.stderr.write(f"Error getting info for process {proc_id} during listing: {e}\n")
                    continue
        return processes_info

    async def clean_processes(self, process_ids: List[str]) -> Dict[str, str]:
        if not process_ids:
            raise ValueError("Process IDs list cannot be empty.")
        
        results: Dict[str, str] = {}
        for proc_id in process_ids:
            async with self._lock:
                process = self._processes.get(proc_id)
            
            if not process:
                results[proc_id] = "Process not found."
                continue
            
            try:
                # Ensure the process is not running before cleaning
                current_status = (await process.get_details()).status
                if current_status == ProcessStatus.RUNNING:
                    results[proc_id] = "Cannot clean a running process. Please stop it first."
                    continue

                clean_result = await process.clean()
                async with self._lock:
                    if proc_id in self._processes:
                        del self._processes[proc_id]
                results[proc_id] = clean_result
            except Exception as e:
                results[proc_id] = f"Failed to clean: {e}"
        return results

    async def shutdown(self) -> None:
        running_processes_ids = []
        async with self._lock:
            for proc_id, process in self._processes.items():
                if (await process.get_details()).status == ProcessStatus.RUNNING:
                    running_processes_ids.append(proc_id)
        
        # Stop all running processes
        for proc_id in running_processes_ids:
            try:
                await self.stop_process(proc_id, force=True, reason="Shutting down process manager.")
            except Exception as e:
                sys.stderr.write(f"Error stopping process {proc_id} during shutdown: {e}\n")
        
        # Clear all outputs and internal process tracking
        async with self._lock:
            all_process_ids = list(self._processes.keys())
        
        if all_process_ids:
            try:
                # Clean up any remaining process outputs
                await self.clean_processes(all_process_ids)
            except Exception as e:
                sys.stderr.write(f"Error cleaning processes during shutdown: {e}\n")

        async with self._lock:
            self._processes.clear() # Ensure the dictionary is empty

    async def get_process(self, process_id: str) -> IProcess:
        async with self._lock:
            process = self._processes.get(process_id)
            if not process:
                raise ProcessNotFoundError(f"Process {process_id} not found.")
            return process
