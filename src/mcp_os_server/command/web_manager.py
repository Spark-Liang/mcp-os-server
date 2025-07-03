"""
Web Manager Implementation for MCP Command Server.

This module provides a FastAPI-based web interface for managing background processes.
It implements the IWebManager interface and provides both web UI and REST API endpoints.
"""

import asyncio
import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .exceptions import ProcessNotFoundError, WebInterfaceError
from .interfaces import ICommandExecutor, IWebManager
from .models import ProcessInfo, ProcessStatus


class StopProcessRequest(BaseModel):
    """Request model for stopping a process."""
    force: bool = False


class WebManager:
    """
    Web Manager implementation for providing web-based process management interface.
    
    This class implements the IWebManager interface and provides:
    - Web UI for process monitoring and management
    - REST API endpoints for programmatic access
    - Real-time process output viewing
    - Process filtering and control capabilities
    """

    def __init__(self) -> None:
        """Initialize the Web Manager."""
        self._app = FastAPI(title="MCP Command Server", description="Process Management Interface")
        self._command_executor: Optional[ICommandExecutor] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_thread: Optional[threading.Thread] = None
        self._server = None
        self._logger = logging.getLogger(__name__)
        self._templates: Optional[Jinja2Templates] = None
        self._setup_routes()

    async def initialize(self, command_executor: ICommandExecutor) -> None:
        """
        Initialize the Web Manager with command executor.
        
        Args:
            command_executor: The command executor instance for process management.
            
        Raises:
            IOError: If an IO error occurs during initialization.
            Exception: For any other unexpected errors during initialization.
        """
        try:
            self._command_executor = command_executor
            # Save the current event loop as the main loop
            self._main_loop = asyncio.get_running_loop()
            self._setup_template_folder()
            self._logger.info("WebManager initialized successfully")
        except Exception as e:
            self._logger.error(f"Failed to initialize WebManager: {e}")
            raise

    def _setup_template_folder(self) -> None:
        """Setup FastAPI template folder path."""
        current_dir = Path(__file__).parent
        template_dir = current_dir / "web_manager_templates"
        self._templates = Jinja2Templates(directory=str(template_dir))

    def _setup_routes(self) -> None:
        """Setup FastAPI routes for web interface and API endpoints."""
        # Web UI routes
        self._app.get("/", response_class=HTMLResponse)(self._index)
        self._app.get("/process/{process_id}", response_class=HTMLResponse)(self._process_detail)
        self._app.get("/debug/threads", response_class=HTMLResponse)(self._debug_threads)
        self._app.get("/debug/tasks", response_class=HTMLResponse)(self._debug_tasks)
        
        # API routes
        self._app.get("/api/processes")(self._api_get_processes)
        self._app.get("/api/processes/{process_id}")(self._api_get_process_detail)
        self._app.get("/api/processes/{process_id}/output")(self._api_get_process_output)
        self._app.post("/api/processes/{process_id}/stop")(self._api_stop_process)
        self._app.post("/api/processes/{process_id}/clean")(self._api_clean_process)
        self._app.get("/api/debug/threads")(self._api_get_thread_stacks)
        self._app.get("/api/debug/threads/download")(self._api_download_thread_stacks)
        self._app.get("/api/debug/tasks")(self._api_get_event_loop_tasks)
        self._app.get("/api/debug/tasks/download")(self._api_download_event_loop_tasks)

    async def _execute_in_main_loop(self, coro):
        """
        Execute a coroutine in the main event loop to avoid event loop mismatch issues.
        
        This method handles the event loop mismatch between the web interface thread
        and the main application thread.
        
        Args:
            coro: The coroutine to execute
            
        Returns:
            The result of the coroutine execution
            
        Raises:
            WebInterfaceError: If the main event loop is not available
        """
        if not self._main_loop:
            # If no main loop stored, execute directly (likely in test environment)
            return await coro
        
        try:
            # Try to execute directly first - this works in most cases including tests
            return await coro
        except RuntimeError as e:
            if "different event loop" in str(e) or "bound to a different event loop" in str(e):
                # Only use run_coroutine_threadsafe if we get a specific event loop error
                try:
                    future = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
                    return future.result()
                except Exception as threadsafe_error:
                    # If threadsafe also fails, re-raise the original error
                    raise e from threadsafe_error
            else:
                # For other RuntimeErrors, re-raise
                raise

    async def start_web_interface(self,
                                  host: str = "0.0.0.0",
                                  port: Optional[int] = None,
                                  debug: bool = False,
                                  url_prefix: str = "") -> None:
        """
        Start the web interface using Uvicorn ASGI server.
        
        Args:
            host: The host address to listen on.
            port: The port to listen on. If None, defaults to 8080.
            debug: Whether to enable debug mode.
            url_prefix: URL prefix for running under a subpath.
            
        Raises:
            WebInterfaceError: If the web interface fails to start.
            ValueError: If the parameters are invalid.
        """
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized with command executor")
        
        try:
            if url_prefix:
                # For URL prefix support, we could use a sub-application
                # For now, we'll note this as a potential enhancement
                self._logger.warning(f"URL prefix '{url_prefix}' is noted but not implemented yet")
            
            # Use Uvicorn server
            self._server_thread = threading.Thread(
                target=self._run_uvicorn_server,
                args=(host, port or 8080, debug),
                daemon=True
            )
            
            self._server_thread.start()
            self._logger.info(f"Web interface started on {host}:{port or 8080}")
            
        except Exception as e:
            error_msg = f"Failed to start web interface: {e}"
            self._logger.error(error_msg)
            raise WebInterfaceError(error_msg) from e

    def _run_uvicorn_server(self, host: str, port: int, debug: bool) -> None:
        """Run Uvicorn server."""
        try:
            config = uvicorn.Config(
                app=self._app,
                host=host,
                port=port,
                log_level="debug" if debug else "info",
                reload=debug,
                access_log=debug
            )
            self._server = uvicorn.Server(config)
            self._server.run()
        except Exception as e:
            self._logger.error(f"Uvicorn server error: {e}")

    # Web UI route handlers
    async def _index(self, request: Request):
        """Render the main process list page."""
        if not self._templates:
            raise HTTPException(status_code=500, detail="Templates not initialized")
        return self._templates.TemplateResponse(request, "process_list.html")

    async def _process_detail(self, request: Request, process_id: str):
        """Render the process detail page."""
        if not self._templates:
            raise HTTPException(status_code=500, detail="Templates not initialized")
        return self._templates.TemplateResponse(
            request, "process_detail.html", {"pid": process_id}
        )

    async def _debug_threads(self, request: Request):
        """Render the thread stack debug page."""
        if not self._templates:
            raise HTTPException(status_code=500, detail="Templates not initialized")
        return self._templates.TemplateResponse(request, "thread_debug.html")

    async def _debug_tasks(self, request: Request):
        """Render the event loop tasks debug page."""
        if not self._templates:
            raise HTTPException(status_code=500, detail="Templates not initialized")
        return self._templates.TemplateResponse(request, "task_debug.html")

    # API route handlers
    async def _api_get_processes(self,
                                status: Optional[str] = Query(None),
                                labels: Optional[str] = Query(None)):
        """API endpoint to get process list with optional filtering."""
        try:
            # Parse filter parameters
            process_status = None
            if status:
                try:
                    process_status = ProcessStatus(status)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status: {status}. Must be one of {', '.join([s.value for s in ProcessStatus])}"
                    )
            
            parsed_labels = None
            if labels:
                parsed_labels = [label.strip() for label in labels.split(',')]
            
            # Get processes using async method
            processes = await self.get_processes(labels=parsed_labels, status=process_status)
            
            # Convert to JSON-serializable format
            process_data = [self._process_info_to_dict(p) for p in processes]
            
            return {
                'success': True,
                'data': process_data,
                'count': len(process_data)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            self._logger.error(f"Error getting processes: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_get_process_detail(self, process_id: str):
        """API endpoint to get detailed process information."""
        try:
            process_info = await self.get_process_detail(process_id)
            return {
                'success': True,
                'data': self._process_info_to_dict(process_info)
            }
            
        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            self._logger.error(f"Error getting process detail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_get_process_output(self,
                                     process_id: str,
                                     tail: Optional[int] = Query(None),
                                     with_stdout: bool = Query(True),
                                     with_stderr: bool = Query(False),
                                     since: Optional[str] = Query(None),
                                     until: Optional[str] = Query(None)):
        """API endpoint to get process output."""
        try:
            # Parse datetime parameters
            since_dt = None
            until_dt = None
            
            if since:
                try:
                    since_dt = datetime.fromisoformat(since)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid since timestamp: {since}")
            
            if until:
                try:
                    until_dt = datetime.fromisoformat(until)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid until timestamp: {until}")
            
            output = await self.get_process_output(
                process_id, tail=tail, since=since_dt, until=until_dt,
                with_stdout=with_stdout, with_stderr=with_stderr
            )
            
            return {
                'success': True,
                'data': output
            }
            
        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            self._logger.error(f"Error getting process output: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_stop_process(self, process_id: str, request: StopProcessRequest):
        """API endpoint to stop a process."""
        try:
            result = await self.stop_process(process_id, force=request.force)
            
            return {
                'success': True,
                'data': result
            }
            
        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            self._logger.error(f"Error stopping process: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_clean_process(self, process_id: str):
        """API endpoint to clean a process."""
        try:
            result = await self.clean_process(process_id)
            
            return {
                'success': True,
                'data': result
            }
            
        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            self._logger.error(f"Error cleaning process: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_get_thread_stacks(self):
        """API endpoint to get current thread stack traces."""
        try:
            thread_data = self._get_current_thread_stacks()
            return {
                'success': True,
                'data': thread_data
            }
        except Exception as e:
            self._logger.error(f"Error getting thread stacks: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_download_thread_stacks(self):
        """API endpoint to download thread stack traces as a text file."""
        try:
            thread_data = self._get_current_thread_stacks()
            
            # Generate text content
            text_content = self._generate_thread_stacks_text(thread_data)
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"thread_stacks_{timestamp}.txt"
            
            # Return file response
            return Response(
                content=text_content,
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
            
        except Exception as e:
            self._logger.error(f"Error downloading thread stacks: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_get_event_loop_tasks(self):
        """API endpoint to get current event loop tasks."""
        try:
            task_data = self._get_current_event_loop_tasks()
            return {
                'success': True,
                'data': task_data
            }
        except Exception as e:
            self._logger.error(f"Error getting event loop tasks: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_download_event_loop_tasks(self):
        """API endpoint to download event loop tasks as a text file."""
        try:
            task_data = self._get_current_event_loop_tasks()
            
            # Generate text content
            text_content = self._generate_event_loop_tasks_text(task_data)
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"event_loop_tasks_{timestamp}.txt"
            
            # Return file response
            return Response(
                content=text_content,
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
            
        except Exception as e:
            self._logger.error(f"Error downloading event loop tasks: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # IWebManager interface implementation
    async def get_processes(self,
                            labels: Optional[List[str]] = None,
                            status: Optional[ProcessStatus] = None) -> List[ProcessInfo]:
        """Get process list with optional filtering."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            return await self._execute_in_main_loop(
                self._command_executor.list_process(status=status, labels=labels)
            )
        except Exception as e:
            self._logger.error(f"Error getting processes: {e}")
            raise WebInterfaceError(f"Failed to get processes: {e}") from e

    async def get_process_detail(self, pid: str) -> ProcessInfo:
        """Get detailed information for a single process."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            return await self._execute_in_main_loop(
                self._command_executor.get_process_detail(pid)
            )
        except ProcessNotFoundError:
            raise
        except Exception as e:
            self._logger.error(f"Error getting process detail: {e}")
            raise WebInterfaceError(f"Failed to get process detail: {e}") from e

    async def get_process_output(self,
                                 pid: str,
                                 tail: Optional[int] = None,
                                 since: Optional[datetime] = None,
                                 until: Optional[datetime] = None,
                                 with_stdout: bool = True,
                                 with_stderr: bool = False) -> Dict[str, List[Dict]]:
        """Get process output."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            result = {'stdout': [], 'stderr': []}
            
            # Convert datetime to timestamp
            since_ts = since.timestamp() if since else None
            until_ts = until.timestamp() if until else None
            
            # Store reference to avoid repeated None checks
            command_executor = self._command_executor
            
            if with_stdout:
                async def get_stdout_logs():
                    logs = []
                    async for entry in command_executor.get_process_logs(
                        pid, "stdout", since=since_ts, until=until_ts, tail=tail
                    ):
                        logs.append({
                            'timestamp': entry.timestamp,
                            'content': entry.text,
                            'output_key': entry.output_key
                        })
                    return logs
                
                result['stdout'] = await self._execute_in_main_loop(get_stdout_logs())
                    
            if with_stderr:
                async def get_stderr_logs():
                    logs = []
                    async for entry in command_executor.get_process_logs(
                        pid, "stderr", since=since_ts, until=until_ts, tail=tail
                    ):
                        logs.append({
                            'timestamp': entry.timestamp,
                            'content': entry.text,
                            'output_key': entry.output_key
                        })
                    return logs
                
                result['stderr'] = await self._execute_in_main_loop(get_stderr_logs())
            
            return result
            
        except ProcessNotFoundError:
            raise
        except Exception as e:
            self._logger.error(f"Error getting process output: {e}")
            raise WebInterfaceError(f"Failed to get process output: {e}") from e

    async def stop_process(self, pid: str, force: bool = False) -> Dict[str, str]:
        """Stop the specified process."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            await self._execute_in_main_loop(
                self._command_executor.stop_process(pid, force=force)
            )
            return {
                'message': f"Process {pid} stopped successfully",
                'pid': pid,
                'action': 'stop',
                'force': str(force)
            }
        except ProcessNotFoundError:
            raise
        except Exception as e:
            self._logger.error(f"Error stopping process: {e}")
            raise WebInterfaceError(f"Failed to stop process: {e}") from e

    async def clean_process(self, pid: str) -> Dict[str, str]:
        """Clean the specified process."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            result = await self._execute_in_main_loop(
                self._command_executor.clean_process([pid])
            )
            return {
                'message': f"Process {pid} cleaned successfully",
                'pid': pid,
                'action': 'clean',
                'result': result.get(pid, 'Unknown')
            }
        except ProcessNotFoundError:
            raise
        except Exception as e:
            self._logger.error(f"Error cleaning process: {e}")
            raise WebInterfaceError(f"Failed to clean process: {e}") from e

    async def clean_all_processes(self) -> Dict[str, Union[str, int]]:
        """Clean all completed or failed processes."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            # Get all processes that can be cleaned
            all_processes = await self.get_processes()
            cleanable_pids = [
                p.pid for p in all_processes 
                if p.status in [ProcessStatus.COMPLETED, ProcessStatus.FAILED, ProcessStatus.ERROR]
            ]
            
            if not cleanable_pids:
                return {
                    'message': 'No processes to clean',
                    'cleaned_count': 0
                }
            
            results = await self._execute_in_main_loop(
                self._command_executor.clean_process(cleanable_pids)
            )
            cleaned_count = len([r for r in results.values() if 'success' in r.lower()])
            
            return {
                'message': f'Cleaned {cleaned_count} processes',
                'cleaned_count': cleaned_count,
                'total_attempted': len(cleanable_pids)
            }
            
        except Exception as e:
            self._logger.error(f"Error cleaning all processes: {e}")
            raise WebInterfaceError(f"Failed to clean all processes: {e}") from e

    async def clean_selected_processes(self, pids: List[str]) -> Dict[str, List[Dict]]:
        """Clean selected processes."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        if not pids:
            raise ValueError("Process ID list cannot be empty")
        
        try:
            results = await self._execute_in_main_loop(
                self._command_executor.clean_process(pids)
            )
            
            successful = []
            failed = []
            running = []
            not_found = []
            
            for pid, result in results.items():
                result_info = {'pid': pid, 'result': result}
                
                if 'success' in result.lower():
                    successful.append(result_info)
                elif 'not found' in result.lower():
                    not_found.append(result_info)
                elif 'running' in result.lower():
                    running.append(result_info)
                else:
                    failed.append(result_info)
            
            return {
                'successful': successful,
                'failed': failed,
                'running': running,
                'not_found': not_found
            }
            
        except Exception as e:
            self._logger.error(f"Error cleaning selected processes: {e}")
            raise WebInterfaceError(f"Failed to clean selected processes: {e}") from e

    async def shutdown(self) -> None:
        """Shutdown the Web Manager and release all resources."""
        try:
            if self._server:
                # Shutdown Uvicorn server
                self._server.should_exit = True
            
            if self._server_thread and self._server_thread.is_alive():
                # Wait for thread to finish
                self._logger.info("Waiting for web server thread to finish...")
            
            self._command_executor = None
            self._main_loop = None
            self._logger.info("WebManager shutdown completed")
            
        except Exception as e:
            self._logger.error(f"Error during WebManager shutdown: {e}")
            raise

    # Utility methods
    def _process_info_to_dict(self, process_info: ProcessInfo) -> Dict:
        """Convert ProcessInfo to dictionary for JSON serialization."""
        # Calculate duration if both start and end times are available
        duration = None
        if process_info.start_time and process_info.end_time:
            duration = (process_info.end_time - process_info.start_time).total_seconds()
        
        return {
            'pid': process_info.pid,
            'command': process_info.command,
            'description': process_info.description,
            'status': process_info.status.value,
            'start_time': process_info.start_time.isoformat() if process_info.start_time else None,
            'end_time': process_info.end_time.isoformat() if process_info.end_time else None,
            'exit_code': process_info.exit_code,
            'directory': process_info.directory,
            'timeout': process_info.timeout,
            'labels': process_info.labels or [],
            'duration': duration
        } 

    def _get_current_thread_stacks(self) -> Dict[str, Any]:
        """
        Get thread stack traces for the current Python process.
        
        Returns:
            Dict containing thread information and stack traces.
        """
        thread_info = {}
        current_frames = sys._current_frames()
        
        for thread_id, frame in current_frames.items():
            # Get thread object from thread_id
            thread_obj = None
            for thread in threading.enumerate():
                if thread.ident == thread_id:
                    thread_obj = thread
                    break
            
            thread_name = thread_obj.name if thread_obj else f"Thread-{thread_id}"
            is_daemon = thread_obj.daemon if thread_obj else False
            is_alive = thread_obj.is_alive() if thread_obj else True
            
            # Extract stack trace
            stack_trace = traceback.format_stack(frame)
            
            thread_info[str(thread_id)] = {
                'thread_id': thread_id,
                'thread_name': thread_name,
                'is_daemon': is_daemon,
                'is_alive': is_alive,
                'stack_trace': stack_trace,
                'stack_summary': ''.join(stack_trace)
            }
        
        return {
            'timestamp': datetime.now().isoformat(),
            'total_threads': len(thread_info),
            'main_thread_id': threading.main_thread().ident,
            'current_thread_id': threading.current_thread().ident,
            'threads': thread_info
        }

    def _generate_thread_stacks_text(self, thread_data: Dict[str, Any]) -> str:
        """Generate thread stack traces as a text file."""
        lines = []
        
        # Add header information
        lines.append("=" * 80)
        lines.append("PYTHON THREAD STACK TRACES")
        lines.append("=" * 80)
        lines.append(f"Timestamp: {thread_data.get('timestamp', 'Unknown')}")
        lines.append(f"Total Threads: {thread_data.get('total_threads', 0)}")
        lines.append(f"Main Thread ID: {thread_data.get('main_thread_id', 'Unknown')}")
        lines.append(f"Current Thread ID: {thread_data.get('current_thread_id', 'Unknown')}")
        lines.append("=" * 80)
        lines.append("")
        
        # Add thread information
        threads = thread_data.get('threads', {})
        for thread_id, info in threads.items():
            lines.append(f"Thread ID: {thread_id}")
            lines.append(f"Thread Name: {info.get('thread_name', 'Unknown')}")
            lines.append(f"Is Daemon: {info.get('is_daemon', False)}")
            lines.append(f"Is Alive: {info.get('is_alive', True)}")
            lines.append("-" * 60)
            lines.append("Stack Trace:")
            
            # Add stack trace lines
            stack_trace = info.get('stack_trace', [])
            if isinstance(stack_trace, list):
                for line in stack_trace:
                    lines.append(line.rstrip())
            else:
                lines.append(str(stack_trace))
            
            lines.append("")
            lines.append("=" * 80)
            lines.append("")
        
        return "\n".join(lines)

    def _get_current_event_loop_tasks(self) -> Dict[str, Any]:
        """
        Get current event loop tasks for debugging.
        
        Returns:
            Dict containing task information and details.
        """
        # Use the main loop if available, otherwise try to get current loop
        loop = self._main_loop
        if not loop:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No event loop running
                return {
                    'timestamp': datetime.now().isoformat(),
                    'event_loop_running': False,
                    'total_tasks': 0,
                    'tasks': {},
                    'error': 'No event loop running'
                }
        
        # Get all tasks for the current event loop
        all_tasks = asyncio.all_tasks(loop)
        task_info = {}
        
        for i, task in enumerate(all_tasks):
            task_id = f"task_{i}"
            
            # Get task name (if available)
            task_name = getattr(task, '_name', None) or getattr(task, 'get_name', lambda: 'Unknown')()
            
            # Get task state
            if task.done():
                if task.cancelled():
                    state = 'cancelled'
                elif task.exception():
                    state = 'exception'
                else:
                    state = 'done'
            else:
                state = 'running'
            
            # Get coroutine information
            coro = getattr(task, '_coro', None)
            coro_name = None
            coro_filename = None
            coro_lineno = None
            
            if coro:
                coro_name = getattr(coro, '__name__', str(coro))
                coro_frame = getattr(coro, 'cr_frame', None) or getattr(coro, 'gi_frame', None)
                if coro_frame:
                    coro_filename = coro_frame.f_code.co_filename
                    coro_lineno = coro_frame.f_lineno
            
            # Get stack trace if running
            stack_trace = []
            if not task.done():
                try:
                    stack_trace = traceback.format_stack(task.get_stack()[0]) if task.get_stack() else []
                except:
                    stack_trace = ['Stack trace unavailable']
            
            task_info[task_id] = {
                'task_id': task_id,
                'task_name': task_name,
                'state': state,
                'done': task.done(),
                'cancelled': task.cancelled() if task.done() else False,
                'coro_name': coro_name,
                'coro_filename': coro_filename,
                'coro_lineno': coro_lineno,
                'stack_trace': stack_trace,
                'stack_summary': ''.join(stack_trace) if stack_trace else ''
            }
            
            # Add exception info if available
            if task.done() and not task.cancelled():
                try:
                    exception = task.exception()
                    if exception:
                        task_info[task_id]['exception'] = str(exception)
                        task_info[task_id]['exception_type'] = type(exception).__name__
                except:
                    pass
        
        return {
            'timestamp': datetime.now().isoformat(),
            'event_loop_running': True,
            'total_tasks': len(all_tasks),
            'loop_id': id(loop),
            'loop_running': loop.is_running(),
            'loop_closed': loop.is_closed(),
            'tasks': task_info
        }

    def _generate_event_loop_tasks_text(self, task_data: Dict[str, Any]) -> str:
        """Generate event loop tasks as a text file."""
        lines = []
        
        # Add header information
        lines.append("=" * 80)
        lines.append("PYTHON EVENT LOOP TASKS")
        lines.append("=" * 80)
        lines.append(f"Timestamp: {task_data.get('timestamp', 'Unknown')}")
        lines.append(f"Event Loop Running: {task_data.get('event_loop_running', False)}")
        lines.append(f"Total Tasks: {task_data.get('total_tasks', 0)}")
        lines.append(f"Loop ID: {task_data.get('loop_id', 'Unknown')}")
        lines.append(f"Loop Running: {task_data.get('loop_running', 'Unknown')}")
        lines.append(f"Loop Closed: {task_data.get('loop_closed', 'Unknown')}")
        lines.append("=" * 80)
        lines.append("")
        
        # Add task information
        tasks = task_data.get('tasks', {})
        if not tasks:
            lines.append("No tasks found or event loop not running.")
            return "\n".join(lines)
        
        for task_id, info in tasks.items():
            lines.append(f"Task ID: {task_id}")
            lines.append(f"Task Name: {info.get('task_name', 'Unknown')}")
            lines.append(f"State: {info.get('state', 'Unknown')}")
            lines.append(f"Done: {info.get('done', False)}")
            lines.append(f"Cancelled: {info.get('cancelled', False)}")
            lines.append(f"Coroutine: {info.get('coro_name', 'Unknown')}")
            
            coro_file = info.get('coro_filename', 'Unknown')
            coro_line = info.get('coro_lineno', 'Unknown')
            lines.append(f"Location: {coro_file}:{coro_line}")
            
            if info.get('exception'):
                lines.append(f"Exception: {info.get('exception_type', 'Unknown')} - {info.get('exception', '')}")
            
            lines.append("-" * 60)
            lines.append("Stack Trace:")
            
            # Add stack trace lines
            stack_trace = info.get('stack_trace', [])
            if isinstance(stack_trace, list) and stack_trace:
                for line in stack_trace:
                    lines.append(line.rstrip())
            else:
                lines.append("No stack trace available")
            
            lines.append("")
            lines.append("=" * 80)
            lines.append("")
        
        return "\n".join(lines)