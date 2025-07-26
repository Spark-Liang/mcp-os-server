"""
Test suite for WebManager API interfaces.

This module provides comprehensive tests for the WebManager class,
testing both web UI and REST API endpoints using real command execution.
"""

import anyio
import json
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from mcp_os_server.command.output_manager import OutputManager
from mcp_os_server.command.process_manager_anyio import AnyioProcessManager
from mcp_os_server.command.interfaces import IProcessManager
from mcp_os_server.command.web_manager import WebManager
from mcp_os_server.command.models import ProcessStatus

from .integration_test_utils import CMD_SCRIPT_PATH


@pytest_asyncio.fixture
async def output_manager(tmp_path: Path) -> AsyncGenerator[OutputManager, None]:
    """Provides a function-scoped OutputManager instance."""
    manager = OutputManager(output_storage_path=tmp_path.as_posix())
    yield manager
    await manager.shutdown()


@pytest_asyncio.fixture
async def process_manager(
    output_manager: OutputManager,
) -> AsyncGenerator[IProcessManager, None]:
    """Provides a function-scoped ProcessManager instance."""
    manager = AnyioProcessManager(
        output_manager=output_manager, process_retention_seconds=5
    )
    await manager.initialize()
    yield manager
    await manager.shutdown()


@pytest_asyncio.fixture
async def command_executor(process_manager: IProcessManager) -> IProcessManager:
    """Fixture providing a ProcessManager instance as command executor."""
    return process_manager


@pytest_asyncio.fixture
async def web_manager():
    """Fixture providing a WebManager instance."""
    return WebManager()


@pytest_asyncio.fixture
async def initialized_web_manager(web_manager, command_executor):
    """Fixture providing an initialized WebManager."""
    await web_manager.initialize(command_executor)
    return web_manager


@pytest.fixture
def test_client(initialized_web_manager):
    """Fixture providing a FastAPI test client."""
    return TestClient(initialized_web_manager._app)


class TestWebManagerInitialization:
    """Test WebManager initialization functionality."""

    @pytest.mark.anyio
    async def test_initialize_success(self, web_manager, command_executor):
        """Test successful WebManager initialization."""
        await web_manager.initialize(command_executor)

        assert web_manager._process_manager is command_executor
        assert web_manager._app is not None

    @pytest.mark.anyio
    async def test_initialize_with_none_executor(self, web_manager):
        """Test WebManager initialization with None executor."""
        # WebManager accepts None executor during initialization
        # but will raise WebInterfaceError when trying to start web interface
        await web_manager.initialize(None)
        assert web_manager._process_manager is None

        # Should raise error when starting web interface
        with pytest.raises(Exception, match="not initialized"):
            await web_manager.start_web_interface()


class TestWebManagerHTTPAPI:
    """Test WebManager HTTP API endpoints with real command execution."""

    @pytest.mark.anyio
    async def test_api_get_processes_empty(self, test_client):
        """Test GET /api/processes endpoint with no processes."""
        response = test_client.get("/api/processes")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert data["count"] == 0

    @pytest.mark.anyio
    async def test_api_get_processes_with_real_process(self, test_client, command_executor, tmp_path):
        """Test GET /api/processes endpoint with real running process."""
        # Start a background process
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "echo", "hello world"],
            directory=tmp_path.as_posix(),
            description="Test echo process",
            labels=["test", "echo"]
        )

        # Wait a moment for process to be recorded
        await anyio.sleep(0.1)

        # Get processes via API
        response = test_client.get("/api/processes")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] >= 1

        # Find our process
        found_process = None
        for proc_data in data["data"]:
            if proc_data["pid"] == process.pid:
                found_process = proc_data
                break

        assert found_process is not None
        assert found_process["description"] == "Test echo process"
        assert "test" in found_process["labels"]
        assert "echo" in found_process["labels"]

        # Wait for process to complete
        await process.wait_for_completion(timeout=10)

    @pytest.mark.anyio
    async def test_api_get_processes_filter_status(self, test_client, command_executor, tmp_path):
        """Test GET /api/processes with status filter."""
        # Start and complete a process
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "echo", "test"],
            directory=tmp_path.as_posix(),
            description="Test completed process"
        )
        await process.wait_for_completion(timeout=10)

        # Test filter by completed status
        response = test_client.get("/api/processes?status=completed")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert all(proc["status"] == "completed" for proc in data["data"])

    @pytest.mark.anyio
    async def test_api_get_processes_filter_labels(self, test_client, command_executor, tmp_path):
        """Test GET /api/processes with labels filter."""
        # Start process with specific labels
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "echo", "test"],
            directory=tmp_path.as_posix(),
            description="Test labeled process",
            labels=["test", "specific"]
        )
        await process.wait_for_completion(timeout=10)

        # Test filter by labels
        response = test_client.get("/api/processes?labels=test,specific")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify all returned processes have the required labels
        for proc_data in data["data"]:
            proc_labels = proc_data.get("labels", [])
            assert any(label in proc_labels for label in ["test", "specific"])

    @pytest.mark.anyio
    async def test_api_get_process_detail_success(self, test_client, command_executor, tmp_path):
        """Test GET /api/processes/<id> endpoint."""
        # Start a process
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "echo", "detail test"],
            directory=tmp_path.as_posix(),
            description="Test process detail"
        )
        await process.wait_for_completion(timeout=10)

        # Get process detail via API
        response = test_client.get(f"/api/processes/{process.pid}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pid"] == process.pid
        assert data["data"]["description"] == "Test process detail"

    @pytest.mark.anyio
    async def test_api_get_process_detail_not_found(self, test_client):
        """Test GET /api/processes/<id> for non-existent process."""
        response = test_client.get("/api/processes/nonexistent-pid")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    @pytest.mark.anyio
    async def test_api_get_process_output_success(self, test_client, command_executor, tmp_path):
        """Test GET /api/processes/<id>/output endpoint."""
        # Start a process that produces output
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "echo", "output test"],
            directory=tmp_path.as_posix(),
            description="Test process output"
        )
        await process.wait_for_completion(timeout=10)

        # Get process output via API
        response = test_client.get(f"/api/processes/{process.pid}/output")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "stdout" in data["data"]
        assert "stderr" in data["data"]

        # Should have some stdout output
        if data["data"]["stdout"]:
            assert any("output test" in entry["content"] for entry in data["data"]["stdout"])

    @pytest.mark.anyio
    async def test_api_get_process_output_with_params(self, test_client, command_executor, tmp_path):
        """Test GET /api/processes/<id>/output with parameters."""
        # Start a process with multiple output lines
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "multiline"],
            directory=tmp_path.as_posix(),
            description="Test multiline output"
        )
        await process.wait_for_completion(timeout=10)

        # Test with tail parameter
        response = test_client.get(
            f"/api/processes/{process.pid}/output?tail=1&with_stdout=true&with_stderr=false"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["stderr"]) == 0
        # Note: tail behavior may vary depending on output content

    @pytest.mark.anyio
    async def test_api_get_process_output_not_found(self, test_client):
        """Test GET /api/processes/<id>/output for non-existent process."""
        response = test_client.get("/api/processes/nonexistent-pid/output")

        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_api_stop_process_success(self, test_client, command_executor, tmp_path):
        """Test POST /api/processes/<id>/stop endpoint."""
        # Start a long-running process
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "sleep", "10"],
            directory=tmp_path.as_posix(),
            description="Test stop process"
        )

        # Give it time to start
        await anyio.sleep(0.1)

        # Stop the process via API
        response = test_client.post(f"/api/processes/{process.pid}/stop", json={"force": False})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pid"] == process.pid
        assert data["data"]["action"] == "stop"

    @pytest.mark.anyio
    async def test_api_stop_process_force(self, test_client, command_executor, tmp_path):
        """Test POST /api/processes/<id>/stop with force."""
        # Start a long-running process
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "sleep", "10"],
            directory=tmp_path.as_posix(),
            description="Test force stop process"
        )

        # Give it time to start
        await anyio.sleep(0.1)

        # Force stop the process via API
        response = test_client.post(f"/api/processes/{process.pid}/stop", json={"force": True})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["force"] == "True"

    @pytest.mark.anyio
    async def test_api_stop_process_not_found(self, test_client):
        """Test POST /api/processes/<id>/stop for non-existent process."""
        response = test_client.post(
            "/api/processes/nonexistent-pid/stop", json={"force": False}
        )

        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_api_clean_process_success(self, test_client, command_executor, tmp_path):
        """Test POST /api/processes/<id>/clean endpoint."""
        # Start and complete a process
        process = await command_executor.start_process(
            command=["python", str(CMD_SCRIPT_PATH), "echo", "clean test"],
            directory=tmp_path.as_posix(),
            description="Test clean process"
        )
        await process.wait_for_completion(timeout=10)

        # Clean the process via API
        response = test_client.post(f"/api/processes/{process.pid}/clean")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["pid"] == process.pid
        assert data["data"]["action"] == "clean"

    @pytest.mark.anyio
    async def test_api_clean_process_not_found(self, test_client):
        """Test POST /api/processes/<id>/clean for non-existent process."""
        response = test_client.post("/api/processes/nonexistent-pid/clean")

        # clean_process API returns 200 with error info in the result, not 404
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "not found" in data["data"]["result"].lower()


class TestWebManagerWebInterface:
    """Test WebManager web interface functionality."""

    def test_index_route(self, test_client):
        """Test the index route returns HTML."""
        response = test_client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_process_detail_route(self, test_client):
        """Test the process detail route returns HTML."""
        response = test_client.get("/process/test-pid")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestWebManagerStartInterface:
    """Test WebManager start web interface functionality."""

    @pytest.mark.anyio
    async def test_start_web_interface_not_initialized(self, web_manager):
        """Test starting web interface when not initialized."""
        with pytest.raises(Exception, match="not initialized"):
            await web_manager.start_web_interface()

    @pytest.mark.anyio
    async def test_start_web_interface_debug_mode(self, initialized_web_manager):
        """Test starting web interface in debug mode."""
        # This test is complex because it involves starting actual servers
        # We'll just test that the method doesn't raise an exception
        # In a real scenario, you might want to mock the server startup

        # Start in a separate thread to avoid blocking
        start_task = None
        
        async def start_server():
            await initialized_web_manager.start_web_interface(
                host="127.0.0.1", port=0, debug=True  # port=0 for random available port
            )

        try:
            # Start the server in background
            async with anyio.create_task_group() as tg:
                tg.start_soon(start_server)
                # Give it a moment to start
                await anyio.sleep(0.1)
                # Cancel the task group to stop the server
                tg.cancel_scope.cancel()
        except anyio.get_cancelled_exc_class():
            # Expected when we cancel the task group
            pass


class TestWebManagerUtilities:
    """Test WebManager utility methods."""

    def test_process_info_to_dict(self, initialized_web_manager, test_client):
        """Test conversion of ProcessInfo to dictionary."""
        # This test needs to check the dict conversion functionality
        # We'll test this through the API endpoints which use this method internally
        response = test_client.get("/api/processes")
        
        # The response should be formatted correctly
        assert response.status_code == 200


class TestWebManagerShutdown:
    """Test WebManager shutdown functionality."""

    @pytest.mark.anyio
    async def test_shutdown_success(self, initialized_web_manager):
        """Test successful shutdown."""
        await initialized_web_manager.shutdown()

        assert initialized_web_manager._process_manager is None

    @pytest.mark.anyio
    async def test_shutdown_with_server(self, initialized_web_manager):
        """Test shutdown with running server."""
        # Mock a running server
        class MockServer:
            def __init__(self):
                self.should_exit = False

        mock_server = MockServer()
        initialized_web_manager._server = mock_server

        await initialized_web_manager.shutdown()

        assert mock_server.should_exit is True
        assert initialized_web_manager._process_manager is None


class TestWebManagerErrorHandling:
    """Test WebManager error handling scenarios."""

    @pytest.mark.anyio
    async def test_process_manager_exception_handling(self, test_client):
        """Test WebManager handles command executor exceptions properly through API."""
        # Test with non-existent process
        response = test_client.get("/api/processes/non-existent")
        assert response.status_code == 404

        data = response.json()
        assert "not found" in data["detail"].lower()

    def test_api_exception_handling(self, test_client):
        """Test API exception handling."""
        # Test with non-existent process
        response = test_client.get("/api/processes/non-existent")
        assert response.status_code == 404

        data = response.json()
        assert "not found" in data["detail"].lower()


class TestWebManagerThreadDebug:
    """Test WebManager thread stack debugging functionality."""

    def test_get_current_thread_stacks_structure(self, initialized_web_manager):
        """Test that _get_current_thread_stacks returns correct structure."""
        result = initialized_web_manager._get_current_thread_stacks()

        # Check top-level structure
        assert isinstance(result, dict)
        assert "timestamp" in result
        assert "total_threads" in result
        assert "main_thread_id" in result
        assert "current_thread_id" in result
        assert "threads" in result

        # Check timestamp format
        assert isinstance(result["timestamp"], str)

        # Check numeric fields
        assert isinstance(result["total_threads"], int)
        assert result["total_threads"] > 0

        # Check threads structure
        threads = result["threads"]
        assert isinstance(threads, dict)
        assert len(threads) == result["total_threads"]

        # Check individual thread structure
        for thread_id, thread_info in threads.items():
            assert isinstance(thread_info, dict)
            assert "thread_id" in thread_info
            assert "thread_name" in thread_info
            assert "is_daemon" in thread_info
            assert "is_alive" in thread_info
            assert "stack_trace" in thread_info
            assert "stack_summary" in thread_info

            # Check data types
            assert isinstance(thread_info["thread_id"], int)
            assert isinstance(thread_info["thread_name"], str)
            assert isinstance(thread_info["is_daemon"], bool)
            assert isinstance(thread_info["is_alive"], bool)
            assert isinstance(thread_info["stack_trace"], list)
            assert isinstance(thread_info["stack_summary"], str)

    def test_api_get_thread_stacks_success(self, test_client):
        """Test /api/debug/threads endpoint returns thread information."""
        response = test_client.get("/api/debug/threads")

        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "data" in data

        thread_data = data["data"]
        assert "timestamp" in thread_data
        assert "total_threads" in thread_data
        assert "main_thread_id" in thread_data
        assert "current_thread_id" in thread_data
        assert "threads" in thread_data

        # Verify we have at least the main thread
        assert thread_data["total_threads"] >= 1
        assert len(thread_data["threads"]) >= 1

    def test_debug_threads_page_renders(self, test_client):
        """Test /debug/threads page renders correctly."""
        response = test_client.get("/debug/threads")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

        # Check that the response contains expected HTML elements
        content = response.text
        assert "Python线程栈调试" in content
        assert "api/debug/threads" in content
        assert "refreshThreads" in content
        assert "downloadThreadStacks" in content
        assert "📥 下载线程栈" in content

    def test_main_thread_exists_in_stack(self, initialized_web_manager):
        """Test that the main thread is always present in thread stacks."""
        result = initialized_web_manager._get_current_thread_stacks()

        threads = result["threads"]
        main_thread_id = result["main_thread_id"]

        # Main thread should exist in the threads dictionary
        assert str(main_thread_id) in threads

        # Find the main thread and verify its properties
        main_thread_found = False
        for thread_info in threads.values():
            if thread_info["thread_name"] == "MainThread":
                main_thread_found = True
                assert thread_info["is_alive"] is True
                break

        assert main_thread_found, "MainThread should be found in thread list"

    def test_thread_stack_contains_function_names(self, initialized_web_manager):
        """Test that thread stacks contain recognizable function names."""
        result = initialized_web_manager._get_current_thread_stacks()

        threads = result["threads"]

        # At least one thread should have stack traces with function names
        found_function_traces = False
        for thread_info in threads.values():
            stack_summary = thread_info["stack_summary"]
            stack_trace = thread_info["stack_trace"]

            if stack_trace and len(stack_trace) > 0:
                # Check that stack trace contains file names and function names
                if ".py" in stack_summary and "in " in stack_summary:
                    found_function_traces = True
                    break

        assert (
            found_function_traces
        ), "At least one thread should have meaningful stack traces"

    @pytest.mark.anyio
    async def test_api_thread_stacks_concurrent_access(self, test_client):
        """Test that thread stack API can handle concurrent requests."""
        import concurrent.futures

        def make_request():
            response = test_client.get("/api/debug/threads")
            return response.status_code

        # Make multiple concurrent requests using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request) for _ in range(5)]
            results = [
                future.result() for future in concurrent.futures.as_completed(futures)
            ]

        # All requests should succeed
        assert all(status == 200 for status in results)
        assert len(results) == 5

    def test_api_download_thread_stacks(self, test_client):
        """Test /api/debug/threads/download endpoint returns text file."""
        response = test_client.get("/api/debug/threads/download")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "thread_stacks_" in response.headers.get("content-disposition", "")

        # Check that the response contains thread stack information
        content = response.text
        assert "PYTHON THREAD STACK TRACES" in content
        assert "Thread ID:" in content
        assert "Thread Name:" in content
        assert "Stack Trace:" in content

    def test_generate_thread_stacks_text(self, initialized_web_manager):
        """Test _generate_thread_stacks_text method generates proper text format."""
        thread_data = initialized_web_manager._get_current_thread_stacks()
        text_content = initialized_web_manager._generate_thread_stacks_text(thread_data)

        # Check basic structure
        assert isinstance(text_content, str)
        assert len(text_content) > 0

        # Check header
        assert "PYTHON THREAD STACK TRACES" in text_content
        assert "Timestamp:" in text_content
        assert "Total Threads:" in text_content

        # Check thread information
        assert "Thread ID:" in text_content
        assert "Thread Name:" in text_content
        assert "Stack Trace:" in text_content


class TestWebManagerTaskDebug:
    """Test WebManager event loop task debugging functionality."""

    def test_api_get_event_loop_tasks_success(self, test_client):
        """Test /api/debug/tasks endpoint returns task information."""
        response = test_client.get("/api/debug/tasks")

        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "data" in data

        task_data = data["data"]
        assert "timestamp" in task_data
        assert "backend" in task_data
        # With anyio, task introspection may not be available
        assert task_data.get("backend") == "anyio" or task_data.get("note")

    def test_debug_tasks_page_renders(self, test_client):
        """Test /debug/tasks page renders correctly."""
        response = test_client.get("/debug/tasks")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

    def test_api_download_event_loop_tasks(self, test_client):
        """Test /api/debug/tasks/download endpoint returns text file."""
        response = test_client.get("/api/debug/tasks/download")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "event_loop_tasks_" in response.headers.get("content-disposition", "")

        # Check that the response contains task information
        content = response.text
        assert "ANYIO EVENT LOOP INFORMATION" in content
        assert "Backend:" in content
