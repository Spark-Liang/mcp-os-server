"""
Web Manager Implementation for MCP Command Server.

This module provides a Flask-based web interface for managing background processes.
It implements the IWebManager interface and provides both web UI and REST API endpoints.
"""

import asyncio
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

from flask import Flask, jsonify, render_template, request

from .exceptions import ProcessNotFoundError, WebInterfaceError
from .interfaces import ICommandExecutor, IWebManager
from .models import ProcessInfo, ProcessStatus


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
        self._app = Flask(__name__)
        self._command_executor: Optional[ICommandExecutor] = None
        self._server_thread: Optional[threading.Thread] = None
        self._server = None
        self._logger = logging.getLogger(__name__)
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
            self._setup_template_folder()
            self._logger.info("WebManager initialized successfully")
        except Exception as e:
            self._logger.error(f"Failed to initialize WebManager: {e}")
            raise

    def _setup_template_folder(self) -> None:
        """Setup Flask template folder path."""
        current_dir = Path(__file__).parent
        template_dir = current_dir / "web_manager_templates"
        self._app.template_folder = str(template_dir)

    def _setup_routes(self) -> None:
        """Setup Flask routes for web interface and API endpoints."""
        # Web UI routes
        self._app.route("/")(self._index)
        self._app.route("/process/<process_id>")(self._process_detail)
        
        # API routes
        self._app.route("/api/processes")(self._api_get_processes)
        self._app.route("/api/processes/<process_id>")(self._api_get_process_detail)
        self._app.route("/api/processes/<process_id>/output")(self._api_get_process_output)
        self._app.route("/api/processes/<process_id>/stop", methods=["POST"])(self._api_stop_process)
        self._app.route("/api/processes/<process_id>/clean", methods=["POST"])(self._api_clean_process)

    async def start_web_interface(self,
                                  host: str = "0.0.0.0",
                                  port: Optional[int] = None,
                                  debug: bool = False,
                                  url_prefix: str = "") -> None:
        """
        Start the web interface using a production-ready WSGI server.
        
        Args:
            host: The host address to listen on.
            port: The port to listen on. If None, Flask will choose.
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
                self._app.config['APPLICATION_ROOT'] = url_prefix
            
            # Use production server unless in debug mode
            if debug:
                # Use Flask development server for debugging
                self._server_thread = threading.Thread(
                    target=self._run_flask_dev_server,
                    args=(host, port, debug),
                    daemon=True
                )
            else:
                # Use production-ready server
                self._server_thread = threading.Thread(
                    target=self._run_production_server,
                    args=(host, port),
                    daemon=True
                )
            
            self._server_thread.start()
            self._logger.info(f"Web interface started on {host}:{port}")
            
        except Exception as e:
            error_msg = f"Failed to start web interface: {e}"
            self._logger.error(error_msg)
            raise WebInterfaceError(error_msg) from e

    def _run_flask_dev_server(self, host: str, port: Optional[int], debug: bool) -> None:
        """Run Flask development server (for debugging only)."""
        try:
            self._app.run(host=host, port=port, debug=debug, threaded=True)
        except Exception as e:
            self._logger.error(f"Flask development server error: {e}")

    def _run_production_server(self, host: str, port: Optional[int]) -> None:
        """Run production-ready WSGI server."""
        try:
            # Try to use waitress (production-ready, cross-platform)
            try:
                from waitress import serve
                self._logger.info("Using Waitress production server")
                serve(self._app, host=host, port=port or 8080, threads=6)
            except ImportError:
                # Fallback to werkzeug's production server
                self._logger.warning("Waitress not available, using Werkzeug server")
                from werkzeug.serving import make_server
                self._server = make_server(host, port or 8080, self._app, threaded=True)
                self._server.serve_forever()
        except Exception as e:
            self._logger.error(f"Production server error: {e}")

    # Web UI route handlers
    def _index(self):
        """Render the main process list page."""
        return render_template("process_list.html")

    def _process_detail(self, process_id: str):
        """Render the process detail page."""
        return render_template("process_detail.html", process_id=process_id)

    # API route handlers
    def _api_get_processes(self):
        """API endpoint to get process list with optional filtering."""
        try:
            # Extract filter parameters from request
            status_param = request.args.get('status')
            labels_param = request.args.get('labels')
            
            status = ProcessStatus(status_param) if status_param else None
            labels = [label.strip() for label in labels_param.split(',')] if labels_param else None
            
            # Get processes using async method
            processes = asyncio.run(self.get_processes(labels=labels, status=status))
            
            # Convert to JSON-serializable format
            process_data = [self._process_info_to_dict(p) for p in processes]
            
            return jsonify({
                'success': True,
                'data': process_data,
                'count': len(process_data)
            })
            
        except Exception as e:
            self._logger.error(f"Error getting processes: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    def _api_get_process_detail(self, process_id: str):
        """API endpoint to get detailed process information."""
        try:
            process_info = asyncio.run(self.get_process_detail(process_id))
            return jsonify({
                'success': True,
                'data': self._process_info_to_dict(process_info)
            })
            
        except ProcessNotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 404
        except Exception as e:
            self._logger.error(f"Error getting process detail: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    def _api_get_process_output(self, process_id: str):
        """API endpoint to get process output."""
        try:
            # Extract parameters from request
            tail = request.args.get('tail', type=int)
            with_stdout = request.args.get('with_stdout', 'true').lower() == 'true'
            with_stderr = request.args.get('with_stderr', 'false').lower() == 'true'
            
            # Parse datetime parameters
            since_str = request.args.get('since')
            until_str = request.args.get('until')
            
            since = datetime.fromisoformat(since_str) if since_str else None
            until = datetime.fromisoformat(until_str) if until_str else None
            
            output = asyncio.run(
                self.get_process_output(
                    process_id, tail=tail, since=since, until=until,
                    with_stdout=with_stdout, with_stderr=with_stderr
                )
            )
            
            return jsonify({
                'success': True,
                'data': output
            })
            
        except ProcessNotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 404
        except Exception as e:
            self._logger.error(f"Error getting process output: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    def _api_stop_process(self, process_id: str):
        """API endpoint to stop a process."""
        try:
            data = request.get_json() or {}
            force = data.get('force', False)
            
            result = asyncio.run(self.stop_process(process_id, force=force))
            
            return jsonify({
                'success': True,
                'data': result
            })
            
        except ProcessNotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 404
        except Exception as e:
            self._logger.error(f"Error stopping process: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    def _api_clean_process(self, process_id: str):
        """API endpoint to clean a process."""
        try:
            result = asyncio.run(self.clean_process(process_id))
            
            return jsonify({
                'success': True,
                'data': result
            })
            
        except ProcessNotFoundError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 404
        except Exception as e:
            self._logger.error(f"Error cleaning process: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    # IWebManager interface implementation
    async def get_processes(self,
                            labels: Optional[List[str]] = None,
                            status: Optional[ProcessStatus] = None) -> List[ProcessInfo]:
        """Get process list with optional filtering."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            return await self._command_executor.list_process(status=status, labels=labels)
        except Exception as e:
            self._logger.error(f"Error getting processes: {e}")
            raise WebInterfaceError(f"Failed to get processes: {e}") from e

    async def get_process_detail(self, pid: str) -> ProcessInfo:
        """Get detailed information for a single process."""
        if not self._command_executor:
            raise WebInterfaceError("WebManager not initialized")
        
        try:
            return await self._command_executor.get_process_detail(pid)
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
            
            if with_stdout:
                async for entry in self._command_executor.get_process_logs(
                    pid, "stdout", since=since_ts, until=until_ts, tail=tail
                ):
                    result['stdout'].append({
                        'timestamp': entry.timestamp,
                        'content': entry.text,
                        'output_key': entry.output_key
                    })
                    
            if with_stderr:
                async for entry in self._command_executor.get_process_logs(
                    pid, "stderr", since=since_ts, until=until_ts, tail=tail
                ):
                    result['stderr'].append({
                        'timestamp': entry.timestamp,
                        'content': entry.text,
                        'output_key': entry.output_key
                    })
            
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
            await self._command_executor.stop_process(pid, force=force)
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
            result = await self._command_executor.clean_process([pid])
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
            
            results = await self._command_executor.clean_process(cleanable_pids)
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
            results = await self._command_executor.clean_process(pids)
            
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
                # Shutdown production server
                self._server.shutdown()
            
            if self._server_thread and self._server_thread.is_alive():
                # Note: For development server, thread will stop when main process exits
                self._logger.info("Web server thread will be stopped when main process exits")
            
            self._command_executor = None
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