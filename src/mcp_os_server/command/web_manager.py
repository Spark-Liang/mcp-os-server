"""
Web Manager Implementation for MCP Command Server.

This module provides a FastAPI-based web interface for managing background processes.
It implements the IWebManager interface and provides both web UI and REST API endpoints.
"""

import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import os

from .exceptions import ProcessNotFoundError, WebInterfaceError
from .interfaces import IProcessManager
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
        self._app = FastAPI(
            title="MCP Command Server", description="Process Management Interface"
        )
        self._process_manager: Optional[IProcessManager] = None
        self._server_thread: Optional[threading.Thread] = None
        self._server = None
        self._logger = logging.getLogger(__name__)
        self._templates: Optional[Jinja2Templates] = None
        self._setup_routes()

    async def initialize(self, process_manager: IProcessManager) -> None:
        """
        Initialize the Web Manager with process manager.

        Args:
            process_manager: The process manager instance for process management.

        Raises:
            IOError: If an IO error occurs during initialization.
            Exception: For any other unexpected errors during initialization.
        """
        try:
            self._process_manager = process_manager
            self._setup_template_folder()
            self._logger.info("WebManager initialized successfully")
        except Exception as e:
            self._logger.error("Failed to initialize WebManager: %s", e, exc_info=True)
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
        self._app.get("/process/{process_id}", response_class=HTMLResponse)(
            self._process_detail
        )
        self._app.get("/debug/threads", response_class=HTMLResponse)(
            self._debug_threads
        )
        self._app.get("/debug/tasks", response_class=HTMLResponse)(self._debug_tasks)
        self._app.get("/debug/logs", response_class=HTMLResponse)(self._debug_logs)

        # API routes
        self._app.get("/api/processes")(self._api_get_processes)
        self._app.get("/api/processes/{process_id}")(self._api_get_process_detail)
        self._app.get("/api/processes/{process_id}/output")(
            self._api_get_process_output
        )
        self._app.post("/api/processes/{process_id}/stop")(self._api_stop_process)
        self._app.post("/api/processes/{process_id}/clean")(self._api_clean_process)
        self._app.get("/api/debug/threads")(self._api_get_thread_stacks)
        self._app.get("/api/debug/threads/download")(self._api_download_thread_stacks)
        self._app.get("/api/debug/tasks")(self._api_get_event_loop_tasks)
        self._app.get("/api/debug/tasks/download")(self._api_download_event_loop_tasks)
        self._app.get("/api/debug/logs")(self._api_get_logs)

    async def start_web_interface(
        self,
        host: str = "0.0.0.0",
        port: Optional[int] = None,
        debug: bool = False,
        url_prefix: str = "",
    ) -> None:
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
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized with command executor")

        try:
            self._logger.debug(f"Try to start web interface on {host}:{port} with debug: {debug}")
            if url_prefix:
                # For URL prefix support, we could use a sub-application
                # For now, we'll note this as a potential enhancement
                self._logger.warning(
                    "URL prefix '%s' is noted but not implemented yet", url_prefix
                )

            # Use Uvicorn server with anyio backend
            self._server_thread = threading.Thread(
                target=self._run_uvicorn_server,
                args=(host, port or 8080, debug),
                daemon=True,
            )

            self._server_thread.start()
            self._logger.info("Web interface started on %s:%s", host, port or 8080)

        except Exception as e:
            error_msg = "Failed to start web interface: %s" % e
            self._logger.error(error_msg, exc_info=True)
            raise WebInterfaceError(error_msg) from e

    def _run_uvicorn_server(self, host: str, port: int, debug: bool) -> None:
        """Run Uvicorn server with anyio backend."""
        try:
            self._logger.debug(f"Try to run uvicorn server on {host}:{port} with debug: {debug}")
            config = uvicorn.Config(
                app=self._app,
                host=host,
                port=port,
                # log_level="debug" if debug else "info",
                log_config=None,
                reload=False,
                access_log=debug,
                loop="auto",  # Let uvicorn choose the best event loop
            )
            self._logger.info(f"uvicorn config: {config}")
            self._server = uvicorn.Server(config)
            self._server.run()
        except Exception as e:
            self._logger.error("Uvicorn server error: %s", e, exc_info=True)

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

    async def _debug_logs(self, request: Request):
        if not self._templates:
            raise HTTPException(status_code=500, detail="Templates not initialized")
        log_content = self._get_log_content()
        return self._templates.TemplateResponse(
            request, "log_viewer.html", {"log_content": log_content}
        )

    # API route handlers
    async def _api_get_processes(
        self, status: Optional[str] = Query(None), labels: Optional[str] = Query(None)
    ):
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
                        detail=f"Invalid status: {status}. Must be one of {', '.join([s.value for s in ProcessStatus])}",
                    )

            parsed_labels = None
            if labels:
                parsed_labels = [label.strip() for label in labels.split(",")]

            # Get processes using async method
            processes = await self.get_processes(
                labels=parsed_labels, status=process_status
            )

            # Convert to JSON-serializable format
            process_data = [self._process_info_to_dict(p) for p in processes]

            return {"success": True, "data": process_data, "count": len(process_data)}

        except HTTPException:
            raise
        except Exception as e:
            self._logger.error("Error getting processes: %s", e, exc_info=True)
            raise WebInterfaceError("Failed to get processes: %s" % e) from e

    async def _api_get_process_detail(self, process_id: str):
        """API endpoint to get detailed process information."""
        try:
            process_info = await self.get_process_detail(process_id)
            return {"success": True, "data": self._process_info_to_dict(process_info)}

        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            self._logger.error("Error getting process detail: %s", e, exc_info=True)
            raise WebInterfaceError("Failed to get process detail: %s" % e) from e

    async def _api_get_process_output(
        self,
        process_id: str,
        tail: Optional[int] = Query(None),
        with_stdout: bool = Query(True),
        with_stderr: bool = Query(False),
        since: Optional[str] = Query(None),
        until: Optional[str] = Query(None),
    ):
        """API endpoint to get process output."""
        try:
            # Parse datetime parameters
            since_dt = None
            until_dt = None

            if since:
                try:
                    since_dt = datetime.fromisoformat(since)
                except ValueError:
                    raise HTTPException(
                        status_code=400, detail=f"Invalid since timestamp: {since}"
                    )

            if until:
                try:
                    until_dt = datetime.fromisoformat(until)
                except ValueError:
                    raise HTTPException(
                        status_code=400, detail=f"Invalid until timestamp: {until}"
                    )

            output = await self.get_process_output(
                process_id,
                tail=tail,
                since=since_dt,
                until=until_dt,
                with_stdout=with_stdout,
                with_stderr=with_stderr,
            )

            return {"success": True, "data": output}

        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            self._logger.error("Error getting process output: %s", e, exc_info=True)
            raise WebInterfaceError("Failed to get process output: %s" % e) from e

    async def _api_stop_process(self, process_id: str, request: StopProcessRequest):
        """API endpoint to stop a process."""
        try:
            result = await self.stop_process(process_id, force=request.force)

            return {"success": True, "data": result}

        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            self._logger.error("Error stopping process: %s", e, exc_info=True)
            raise WebInterfaceError("Failed to stop process: %s" % e) from e

    async def _api_clean_process(self, process_id: str):
        """API endpoint to clean a process."""
        try:
            result = await self.clean_process(process_id)

            return {"success": True, "data": result}

        except ProcessNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            self._logger.error("Error cleaning process: %s", e, exc_info=True)
            raise WebInterfaceError("Failed to clean process: %s" % e) from e

    async def _api_get_thread_stacks(self):
        """API endpoint to get current thread stack traces."""
        try:
            thread_data = self._get_current_thread_stacks()
            return {"success": True, "data": thread_data}
        except Exception as e:
            self._logger.error("Error getting thread stacks: %s", e, exc_info=True)
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
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        except Exception as e:
            self._logger.error("Error downloading thread stacks: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_get_event_loop_tasks(self):
        """API endpoint to get current event loop tasks."""
        try:
            task_data = self._get_current_event_loop_tasks()
            return {"success": True, "data": task_data}
        except Exception as e:
            self._logger.error("Error getting event loop tasks: %s", e, exc_info=True)
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
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        except Exception as e:
            self._logger.error(
                "Error downloading event loop tasks: %s", e, exc_info=True
            )
            raise HTTPException(status_code=500, detail=str(e))

    async def _api_get_logs(self):
        log_content = self._get_log_content()
        return Response(content=log_content, media_type="text/plain")

    async def shutdown(self) -> None:
        """Shutdown the Web Manager and release all resources."""
        try:
            if self._server:
                # Shutdown Uvicorn server
                self._server.should_exit = True

            if self._server_thread and self._server_thread.is_alive():
                # Wait for thread to finish
                self._logger.info("Waiting for web server thread to finish...")

            self._process_manager = None
            self._logger.info("WebManager shutdown completed")

        except Exception as e:
            self._logger.error("Error during WebManager shutdown: %s", e, exc_info=True)
            raise

    # Business logic methods (delegate to command executor)
    async def get_processes(
        self, labels: Optional[list[str]] = None, status: Optional[ProcessStatus] = None
    ) -> list[ProcessInfo]:
        """Get all processes with optional filtering."""
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            return await self._process_manager.list_processes(
                status=status, labels=labels
            )
        except Exception as e:
            raise WebInterfaceError(f"Failed to get processes: {e}") from e

    async def get_process_detail(self, process_id: str) -> ProcessInfo:
        """Get detailed information for a specific process."""
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            return await self._process_manager.get_process_info(process_id)
        except Exception as e:
            if "not found" in str(e).lower():
                raise ProcessNotFoundError(f"Process {process_id} not found") from e
            raise WebInterfaceError(f"Failed to get process detail: {e}") from e

    async def get_process_output(
        self,
        process_id: str,
        tail: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        with_stdout: bool = True,
        with_stderr: bool = False,
    ) -> Dict[str, list[Dict]]:
        """Get process output."""
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            output = {"stdout": [], "stderr": []}

            # Get the process instance first
            process = await self._process_manager.get_process(process_id)

            # Convert datetime to timestamp if needed
            since_ts = since.timestamp() if since else None
            until_ts = until.timestamp() if until else None

            if with_stdout:
                async for entry in process.get_output(
                    "stdout", since=since_ts, until=until_ts, tail=tail
                ):
                    output["stdout"].append(
                        {
                            "timestamp": entry.timestamp.isoformat(),
                            "content": entry.text,
                        }
                    )

            if with_stderr:
                async for entry in process.get_output(
                    "stderr", since=since_ts, until=until_ts, tail=tail
                ):
                    output["stderr"].append(
                        {
                            "timestamp": entry.timestamp.isoformat(),
                            "content": entry.text,
                        }
                    )

            return output
        except Exception as e:
            if "not found" in str(e).lower():
                raise ProcessNotFoundError(f"Process {process_id} not found") from e
            raise WebInterfaceError(f"Failed to get process output: {e}") from e

    async def stop_process(
        self, process_id: str, force: bool = False
    ) -> Dict[str, str]:
        """Stop a process."""
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            await self._process_manager.stop_process(process_id, force=force)
            return {
                "pid": process_id,
                "action": "stop",
                "force": str(force),
                "message": f"Process {process_id} stopped successfully",
            }
        except Exception as e:
            if "not found" in str(e).lower():
                raise ProcessNotFoundError(f"Process {process_id} not found") from e
            raise WebInterfaceError(f"Failed to stop process: {e}") from e

    async def clean_process(self, process_id: str) -> Dict[str, str]:
        """Clean a process."""
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            result = await self._process_manager.clean_processes([process_id])
            clean_result = result.get(process_id, "Unknown result") or "Unknown result"
            return {
                "pid": process_id,
                "action": "clean",
                "result": clean_result,
                "message": f"Process {process_id} cleaned",
            }
        except Exception as e:
            raise WebInterfaceError(f"Failed to clean process: {e}") from e

    async def clean_all_processes(self) -> Dict[str, Any]:
        """Clean all completed/failed processes."""
        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            # Get all completed/failed processes
            all_processes = await self._process_manager.list_processes()
            cleanable_pids = [
                p.pid
                for p in all_processes
                if p.status
                in [
                    ProcessStatus.COMPLETED,
                    ProcessStatus.FAILED,
                    ProcessStatus.TERMINATED,
                ]
            ]

            if not cleanable_pids:
                return {"cleaned_count": 0, "message": "No processes to clean"}

            results = await self._process_manager.clean_processes(cleanable_pids)
            cleaned_count = len(
                [r for r in results.values() if "success" in str(r).lower()]
            )

            return {
                "cleaned_count": cleaned_count,
                "total_candidates": len(cleanable_pids),
                "message": f"Cleaned {cleaned_count} processes",
            }
        except Exception as e:
            raise WebInterfaceError(f"Failed to clean all processes: {e}") from e

    async def clean_selected_processes(
        self, process_ids: list[str]
    ) -> Dict[str, list[Dict]]:
        """Clean selected processes."""
        if not process_ids:
            raise ValueError("Process ID list cannot be empty")

        if not self._process_manager:
            raise WebInterfaceError("WebManager not initialized")

        try:
            results = await self._process_manager.clean_processes(process_ids)

            successful = []
            not_found = []

            for pid, result in results.items():
                if "success" in str(result).lower():
                    successful.append({"pid": pid, "result": result})
                elif "not found" in str(result).lower():
                    not_found.append({"pid": pid, "result": result})
                else:
                    # Other errors
                    successful.append({"pid": pid, "result": result})

            return {"successful": successful, "not_found": not_found}
        except Exception as e:
            raise WebInterfaceError(f"Failed to clean selected processes: {e}") from e

    # Utility methods
    def _process_info_to_dict(self, process_info: ProcessInfo) -> Dict:
        """Convert ProcessInfo to dictionary for JSON serialization."""
        # Calculate duration if both start and end times are available
        duration = None
        if process_info.start_time and process_info.end_time:
            duration = (process_info.end_time - process_info.start_time).total_seconds()

        return {
            "pid": process_info.pid,
            "command": process_info.command,
            "description": process_info.description,
            "status": process_info.status.value,
            "start_time": (
                process_info.start_time.isoformat() if process_info.start_time else None
            ),
            "end_time": (
                process_info.end_time.isoformat() if process_info.end_time else None
            ),
            "exit_code": process_info.exit_code,
            "directory": process_info.directory,
            "encoding": process_info.encoding,
            "envs": process_info.envs,
            "timeout": process_info.timeout,
            "labels": process_info.labels or [],
            "duration": duration,
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
                "thread_id": thread_id,
                "thread_name": thread_name,
                "is_daemon": is_daemon,
                "is_alive": is_alive,
                "stack_trace": stack_trace,
                "stack_summary": "".join(stack_trace),
            }

        return {
            "timestamp": datetime.now().isoformat(),
            "total_threads": len(thread_info),
            "main_thread_id": threading.main_thread().ident,
            "current_thread_id": threading.current_thread().ident,
            "threads": thread_info,
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
        lines.append(
            f"Current Thread ID: {thread_data.get('current_thread_id', 'Unknown')}"
        )
        lines.append("=" * 80)
        lines.append("")

        # Add thread information
        threads = thread_data.get("threads", {})
        for thread_id, info in threads.items():
            lines.append(f"Thread ID: {thread_id}")
            lines.append(f"Thread Name: {info.get('thread_name', 'Unknown')}")
            lines.append(f"Is Daemon: {info.get('is_daemon', False)}")
            lines.append(f"Is Alive: {info.get('is_alive', True)}")
            lines.append("-" * 60)
            lines.append("Stack Trace:")

            # Add stack trace lines
            stack_trace = info.get("stack_trace", [])
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
        Using anyio-compatible approach.

        Returns:
            Dict containing task information and details.
        """
        try:
            # Since we're using anyio, we can't directly access asyncio tasks
            # Instead, we'll provide basic information about the anyio runtime
            return {
                "timestamp": datetime.now().isoformat(),
                "event_loop_running": True,
                "backend": "anyio",
                "total_tasks": "unknown",
                "tasks": {},
                "note": "Task introspection not available with anyio backend",
            }
        except Exception as e:
            return {
                "timestamp": datetime.now().isoformat(),
                "event_loop_running": False,
                "error": str(e),
                "total_tasks": 0,
                "tasks": {},
            }

    def _generate_event_loop_tasks_text(self, task_data: Dict[str, Any]) -> str:
        """Generate event loop tasks as a text file."""
        lines = []

        # Add header information
        lines.append("=" * 80)
        lines.append("ANYIO EVENT LOOP INFORMATION")
        lines.append("=" * 80)
        lines.append(f"Timestamp: {task_data.get('timestamp', 'Unknown')}")
        lines.append(
            f"Event Loop Running: {task_data.get('event_loop_running', False)}"
        )
        lines.append(f"Backend: {task_data.get('backend', 'Unknown')}")
        lines.append(f"Total Tasks: {task_data.get('total_tasks', 0)}")
        lines.append("=" * 80)
        lines.append("")

        if task_data.get("note"):
            lines.append(f"Note: {task_data['note']}")
            lines.append("")

        if task_data.get("error"):
            lines.append(f"Error: {task_data['error']}")
            lines.append("")

        return "\n".join(lines)

    def _get_log_content(self) -> str:
        log_path = os.getenv('MCP_LOG_FILE')
        if not log_path:
            return "Log file path not set"
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading log: {str(e)}"
