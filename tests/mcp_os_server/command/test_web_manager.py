"""
Test suite for WebManager API interfaces.

This module provides comprehensive tests for the WebManager class,
including both direct method calls and FastAPI API endpoints testing.
"""

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta
from typing import AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from mcp_os_server.command.exceptions import ProcessNotFoundError, WebInterfaceError
from mcp_os_server.command.interfaces import ICommandExecutor
from mcp_os_server.command.models import OutputMessageEntry, ProcessInfo, ProcessStatus
from mcp_os_server.command.web_manager import WebManager


class MockCommandExecutor:
    """Mock implementation of ICommandExecutor for testing."""
    
    def __init__(self):
        """Initialize the mock command executor."""
        self._processes = {}
        self._output_logs = {}
        self._initialize_sample_data()
    
    def _initialize_sample_data(self):
        """Initialize sample process data for testing."""
        now = datetime.now()
        
        # Running process
        self._processes["proc1"] = ProcessInfo(
            pid="proc1",
            command=["echo", "hello"],
            directory="/tmp",
            description="Test echo command",
            status=ProcessStatus.RUNNING,
            start_time=now - timedelta(minutes=5),
            end_time=None,
            exit_code=None,
            timeout=None,
            error_message=None,
            labels=["test", "echo"]
        )
        
        # Completed process
        self._processes["proc2"] = ProcessInfo(
            pid="proc2",
            command=["ls", "-la"],
            directory="/home",
            description="List directory",
            status=ProcessStatus.COMPLETED,
            start_time=now - timedelta(minutes=10),
            end_time=now - timedelta(minutes=8),
            exit_code=0,
            timeout=None,
            error_message=None,
            labels=["completed"]
        )
        
        # Failed process
        self._processes["proc3"] = ProcessInfo(
            pid="proc3",
            command=["false"],
            directory="/tmp",
            description="Always fail command",
            status=ProcessStatus.FAILED,
            start_time=now - timedelta(minutes=15),
            end_time=now - timedelta(minutes=14),
            exit_code=1,
            timeout=None,
            error_message=None,
            labels=["test", "failed"]
        )
        
        # Sample output logs
        self._output_logs = {
            "proc1": {
                "stdout": [
                    OutputMessageEntry(
                        timestamp=now - timedelta(minutes=4),
                        text="hello world",
                        output_key="stdout"
                    ),
                    OutputMessageEntry(
                        timestamp=now - timedelta(minutes=3),
                        text="second line",
                        output_key="stdout"
                    )
                ],
                "stderr": []
            },
            "proc2": {
                "stdout": [
                    OutputMessageEntry(
                        timestamp=now - timedelta(minutes=9),
                        text="total 4",
                        output_key="stdout"
                    ),
                    OutputMessageEntry(
                        timestamp=now - timedelta(minutes=9),
                        text="drwxr-xr-x 2 user user 4096 Dec  1 00:00 test",
                        output_key="stdout"
                    )
                ],
                "stderr": []
            },
            "proc3": {
                "stdout": [],
                "stderr": [
                    OutputMessageEntry(
                        timestamp=now - timedelta(minutes=14),
                        text="Command failed",
                        output_key="stderr"
                    )
                ]
            }
        }
    
    async def list_process(self, status: Optional[ProcessStatus] = None, 
                          labels: Optional[List[str]] = None) -> List[ProcessInfo]:
        """Mock list_process method."""
        processes = list(self._processes.values())
        
        if status:
            processes = [p for p in processes if p.status == status]
        
        if labels:
            processes = [p for p in processes 
                        if any(label in p.labels for label in labels)]
        
        return processes
    
    async def get_process_detail(self, process_id: str) -> ProcessInfo:
        """Mock get_process_detail method."""
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process {process_id} not found")
        return self._processes[process_id]
    
    async def get_process_logs(self, process_id: str, output_key: str,
                              since: Optional[float] = None,
                              until: Optional[float] = None,
                              tail: Optional[int] = None) -> AsyncGenerator[OutputMessageEntry, None]:
        """Mock get_process_logs method."""
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process {process_id} not found")
        
        if process_id not in self._output_logs:
            return
        
        logs = self._output_logs[process_id].get(output_key, [])
        
        # Apply filters
        if since:
            since_dt = datetime.fromtimestamp(since)
            logs = [log for log in logs if log.timestamp >= since_dt]
        
        if until:
            until_dt = datetime.fromtimestamp(until)
            logs = [log for log in logs if log.timestamp <= until_dt]
        
        if tail:
            logs = logs[-tail:]
        
        for log in logs:
            yield log
    
    async def stop_process(self, process_id: str, force: bool = False, 
                          reason: Optional[str] = None) -> None:
        """Mock stop_process method."""
        if process_id not in self._processes:
            raise ProcessNotFoundError(f"Process {process_id} not found")
        
        process = self._processes[process_id]
        if process.status == ProcessStatus.RUNNING:
            process.status = ProcessStatus.TERMINATED
            process.end_time = datetime.now()
    
    async def clean_process(self, process_ids: List[str]) -> Dict[str, str]:
        """Mock clean_process method."""
        results = {}
        for pid in process_ids:
            if pid not in self._processes:
                results[pid] = f"Process {pid} not found"
            elif self._processes[pid].status == ProcessStatus.RUNNING:
                results[pid] = f"Process {pid} is still running"
            else:
                del self._processes[pid]
                if pid in self._output_logs:
                    del self._output_logs[pid]
                results[pid] = f"Process {pid} cleaned successfully"
        
        return results


@pytest_asyncio.fixture
async def mock_command_executor():
    """Fixture providing a mock command executor."""
    return MockCommandExecutor()


@pytest_asyncio.fixture
async def web_manager():
    """Fixture providing a WebManager instance."""
    return WebManager()


@pytest_asyncio.fixture
async def initialized_web_manager(web_manager, mock_command_executor):
    """Fixture providing an initialized WebManager."""
    await web_manager.initialize(mock_command_executor)
    return web_manager


@pytest.fixture
def test_client(initialized_web_manager):
    """Fixture providing a FastAPI test client."""
    return TestClient(initialized_web_manager._app)


class TestWebManagerInitialization:
    """Test WebManager initialization functionality."""
    
    @pytest.mark.asyncio
    async def test_initialize_success(self, web_manager, mock_command_executor):
        """Test successful WebManager initialization."""
        await web_manager.initialize(mock_command_executor)
        
        assert web_manager._command_executor is mock_command_executor
        assert web_manager._app is not None
    
    @pytest.mark.asyncio
    async def test_initialize_with_none_executor(self, web_manager):
        """Test WebManager initialization with None executor."""
        # WebManager accepts None executor during initialization 
        # but will raise WebInterfaceError when trying to start web interface
        await web_manager.initialize(None)
        assert web_manager._command_executor is None
        
        # Should raise error when starting web interface
        with pytest.raises(WebInterfaceError, match="not initialized"):
            await web_manager.start_web_interface()


class TestWebManagerBusinessLogic:
    """Test WebManager business logic methods."""
    
    @pytest.mark.asyncio
    async def test_get_processes_all(self, initialized_web_manager):
        """Test getting all processes."""
        processes = await initialized_web_manager.get_processes()
        
        assert len(processes) == 3
        pids = [p.pid for p in processes]
        assert "proc1" in pids
        assert "proc2" in pids
        assert "proc3" in pids
    
    @pytest.mark.asyncio
    async def test_get_processes_filter_by_status(self, initialized_web_manager):
        """Test getting processes filtered by status."""
        processes = await initialized_web_manager.get_processes(
            status=ProcessStatus.RUNNING
        )
        
        assert len(processes) == 1
        assert processes[0].pid == "proc1"
        assert processes[0].status == ProcessStatus.RUNNING
    
    @pytest.mark.asyncio
    async def test_get_processes_filter_by_labels(self, initialized_web_manager):
        """Test getting processes filtered by labels."""
        processes = await initialized_web_manager.get_processes(labels=["test"])
        
        assert len(processes) == 2
        pids = [p.pid for p in processes]
        assert "proc1" in pids
        assert "proc3" in pids
    
    @pytest.mark.asyncio
    async def test_get_processes_not_initialized(self, web_manager):
        """Test getting processes when not initialized."""
        with pytest.raises(WebInterfaceError, match="WebManager not initialized"):
            await web_manager.get_processes()
    
    @pytest.mark.asyncio
    async def test_get_process_detail_success(self, initialized_web_manager):
        """Test getting process detail successfully."""
        process = await initialized_web_manager.get_process_detail("proc1")
        
        assert process.pid == "proc1"
        assert process.command == ["echo", "hello"]
        assert process.status == ProcessStatus.RUNNING
    
    @pytest.mark.asyncio
    async def test_get_process_detail_not_found(self, initialized_web_manager):
        """Test getting process detail for non-existent process."""
        with pytest.raises(ProcessNotFoundError):
            await initialized_web_manager.get_process_detail("nonexistent")
    
    @pytest.mark.asyncio
    async def test_get_process_output_stdout(self, initialized_web_manager):
        """Test getting process stdout output."""
        output = await initialized_web_manager.get_process_output(
            "proc1", with_stdout=True, with_stderr=False
        )
        
        assert "stdout" in output
        assert "stderr" in output
        assert len(output["stdout"]) == 2
        assert len(output["stderr"]) == 0
        assert output["stdout"][0]["content"] == "hello world"
    
    @pytest.mark.asyncio
    async def test_get_process_output_stderr(self, initialized_web_manager):
        """Test getting process stderr output."""
        output = await initialized_web_manager.get_process_output(
            "proc3", with_stdout=False, with_stderr=True
        )
        
        assert "stdout" in output
        assert "stderr" in output
        assert len(output["stdout"]) == 0
        assert len(output["stderr"]) == 1
        assert output["stderr"][0]["content"] == "Command failed"
    
    @pytest.mark.asyncio
    async def test_get_process_output_with_tail(self, initialized_web_manager):
        """Test getting process output with tail limit."""
        output = await initialized_web_manager.get_process_output(
            "proc1", tail=1, with_stdout=True
        )
        
        assert len(output["stdout"]) == 1
        assert output["stdout"][0]["content"] == "second line"
    
    @pytest.mark.asyncio
    async def test_get_process_output_not_found(self, initialized_web_manager):
        """Test getting output for non-existent process."""
        with pytest.raises(ProcessNotFoundError):
            await initialized_web_manager.get_process_output("nonexistent")
    
    @pytest.mark.asyncio
    async def test_stop_process_success(self, initialized_web_manager):
        """Test stopping a process successfully."""
        result = await initialized_web_manager.stop_process("proc1")
        
        assert result["pid"] == "proc1"
        assert result["action"] == "stop"
        assert "stopped successfully" in result["message"]
    
    @pytest.mark.asyncio
    async def test_stop_process_not_found(self, initialized_web_manager):
        """Test stopping a non-existent process."""
        with pytest.raises(ProcessNotFoundError):
            await initialized_web_manager.stop_process("nonexistent")
    
    @pytest.mark.asyncio
    async def test_clean_process_success(self, initialized_web_manager):
        """Test cleaning a process successfully."""
        result = await initialized_web_manager.clean_process("proc2")
        
        assert result["pid"] == "proc2"
        assert result["action"] == "clean"
        assert "cleaned successfully" in result["message"]
    
    @pytest.mark.asyncio
    async def test_clean_process_not_found(self, initialized_web_manager):
        """Test cleaning a non-existent process."""
        # clean_process doesn't raise ProcessNotFoundError, it returns result info
        result = await initialized_web_manager.clean_process("nonexistent")
        
        assert result["pid"] == "nonexistent"
        assert result["action"] == "clean"
        assert "not found" in result["result"]
    
    @pytest.mark.asyncio
    async def test_clean_all_processes(self, initialized_web_manager):
        """Test cleaning all completed/failed processes."""
        result = await initialized_web_manager.clean_all_processes()
        
        assert "cleaned_count" in result
        assert result["cleaned_count"] >= 1  # At least proc2 should be cleaned
    
    @pytest.mark.asyncio
    async def test_clean_selected_processes(self, initialized_web_manager):
        """Test cleaning selected processes."""
        result = await initialized_web_manager.clean_selected_processes(
            ["proc2", "proc3", "nonexistent"]
        )
        
        assert "successful" in result
        assert "not_found" in result
        assert len(result["successful"]) >= 1
        assert len(result["not_found"]) == 1
    
    @pytest.mark.asyncio
    async def test_clean_selected_processes_empty_list(self, initialized_web_manager):
        """Test cleaning with empty process list."""
        with pytest.raises(ValueError, match="Process ID list cannot be empty"):
            await initialized_web_manager.clean_selected_processes([])


class TestWebManagerFastAPIAPI:
    """Test WebManager FastAPI API endpoints."""
    
    def test_api_get_processes_all(self, test_client):
        """Test GET /api/processes endpoint."""
        response = test_client.get('/api/processes')
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert data["count"] == 3
    
    def test_api_get_processes_filter_status(self, test_client):
        """Test GET /api/processes with status filter."""
        response = test_client.get('/api/processes?status=running')
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["data"][0]["status"] == "running"
    
    def test_api_get_processes_filter_labels(self, test_client):
        """Test GET /api/processes with labels filter."""
        response = test_client.get('/api/processes?labels=test,echo')
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] >= 1
    
    def test_api_get_process_detail_success(self, test_client):
        """Test GET /api/processes/<id> endpoint."""
        response = test_client.get('/api/processes/proc1')
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pid"] == "proc1"
        assert data["data"]["status"] == "running"
    
    def test_api_get_process_detail_not_found(self, test_client):
        """Test GET /api/processes/<id> for non-existent process."""
        response = test_client.get('/api/processes/nonexistent')
        
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
    
    def test_api_get_process_output_success(self, test_client):
        """Test GET /api/processes/<id>/output endpoint."""
        response = test_client.get('/api/processes/proc1/output')
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "stdout" in data["data"]
        assert "stderr" in data["data"]
    
    def test_api_get_process_output_with_params(self, test_client):
        """Test GET /api/processes/<id>/output with parameters."""
        response = test_client.get(
            '/api/processes/proc1/output?tail=1&with_stdout=true&with_stderr=false'
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["stdout"]) == 1
        assert len(data["data"]["stderr"]) == 0
    
    def test_api_get_process_output_not_found(self, test_client):
        """Test GET /api/processes/<id>/output for non-existent process."""
        response = test_client.get('/api/processes/nonexistent/output')
        
        assert response.status_code == 404
    
    def test_api_stop_process_success(self, test_client):
        """Test POST /api/processes/<id>/stop endpoint."""
        response = test_client.post(
            '/api/processes/proc1/stop',
            json={"force": False}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pid"] == "proc1"
    
    def test_api_stop_process_force(self, test_client):
        """Test POST /api/processes/<id>/stop with force."""
        response = test_client.post(
            '/api/processes/proc1/stop',
            json={"force": True}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["force"] == "True"
    
    def test_api_stop_process_not_found(self, test_client):
        """Test POST /api/processes/<id>/stop for non-existent process."""
        response = test_client.post(
            '/api/processes/nonexistent/stop',
            json={"force": False}
        )
        
        assert response.status_code == 404
    
    def test_api_clean_process_success(self, test_client):
        """Test POST /api/processes/<id>/clean endpoint."""
        response = test_client.post('/api/processes/proc2/clean')
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pid"] == "proc2"
    
    def test_api_clean_process_not_found(self, test_client):
        """Test POST /api/processes/<id>/clean for non-existent process."""
        response = test_client.post('/api/processes/nonexistent/clean')
        
        # clean_process API returns 200 with error info in the result, not 404
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "not found" in data["data"]["result"]


class TestWebManagerWebInterface:
    """Test WebManager web interface functionality."""
    
    def test_index_route(self, test_client):
        """Test the index route returns HTML."""
        response = test_client.get('/')
        
        assert response.status_code == 200
        assert 'text/html' in response.headers.get('content-type', '')
    
    def test_process_detail_route(self, test_client):
        """Test the process detail route returns HTML."""
        response = test_client.get('/process/proc1')
        
        assert response.status_code == 200
        assert 'text/html' in response.headers.get('content-type', '')


class TestWebManagerStartInterface:
    """Test WebManager start web interface functionality."""
    
    @pytest.mark.asyncio
    async def test_start_web_interface_not_initialized(self, web_manager):
        """Test starting web interface when not initialized."""
        with pytest.raises(WebInterfaceError, match="not initialized"):
            await web_manager.start_web_interface()
    
    @pytest.mark.asyncio
    async def test_start_web_interface_debug_mode(self, initialized_web_manager):
        """Test starting web interface in debug mode."""
        # This test is complex because it involves starting actual servers
        # We'll just test that the method doesn't raise an exception
        # In a real scenario, you might want to mock the server startup
        
        with patch.object(threading, 'Thread') as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            
            await initialized_web_manager.start_web_interface(
                host="127.0.0.1", port=8080, debug=True
            )
            
            mock_thread.assert_called_once()
            mock_thread_instance.start.assert_called_once()


class TestWebManagerUtilities:
    """Test WebManager utility methods."""
    
    def test_process_info_to_dict(self, initialized_web_manager, mock_command_executor):
        """Test conversion of ProcessInfo to dictionary."""
        process = mock_command_executor._processes["proc2"]  # Completed process
        result = initialized_web_manager._process_info_to_dict(process)
        
        assert result["pid"] == "proc2"
        assert result["status"] == "completed"
        assert result["command"] == ["ls", "-la"]
        assert result["exit_code"] == 0
        assert result["duration"] is not None
        assert isinstance(result["duration"], (int, float))


class TestWebManagerShutdown:
    """Test WebManager shutdown functionality."""
    
    @pytest.mark.asyncio
    async def test_shutdown_success(self, initialized_web_manager):
        """Test successful shutdown."""
        await initialized_web_manager.shutdown()
        
        assert initialized_web_manager._command_executor is None
    
    @pytest.mark.asyncio
    async def test_shutdown_with_server(self, initialized_web_manager):
        """Test shutdown with running server."""
        # Mock a running server
        mock_server = MagicMock()
        initialized_web_manager._server = mock_server
        
        await initialized_web_manager.shutdown()
        
        assert mock_server.should_exit is True
        assert initialized_web_manager._command_executor is None


class TestWebManagerErrorHandling:
    """Test WebManager error handling scenarios."""
    
    @pytest.mark.asyncio
    async def test_command_executor_exception_handling(self, initialized_web_manager):
        """Test handling of command executor exceptions."""
        # Mock the command executor to raise an exception
        initialized_web_manager._command_executor.list_process = AsyncMock(
            side_effect=Exception("Test exception")
        )
        
        with pytest.raises(WebInterfaceError, match="Failed to get processes"):
            await initialized_web_manager.get_processes()
    
    def test_api_exception_handling(self, test_client):
        """Test API exception handling returns proper error response."""
        # This would test the case where the underlying service throws an exception
        # We need to mock the initialized_web_manager to throw an exception
        
        # For now, we test with a malformed request that should cause an error
        response = test_client.get('/api/processes?status=invalid_status')
        
        # This should return an error response
        assert response.status_code == 400
        data = response.json()
        assert "Invalid status" in data["detail"]