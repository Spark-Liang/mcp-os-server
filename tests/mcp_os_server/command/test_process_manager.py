import asyncio
import os
import sys
import time
from datetime import datetime, timedelta
from typing import AsyncGenerator, List, Optional

import pytest
import pytest_asyncio
from mcp_os_server.command.exceptions import CommandExecutionError, ProcessNotFoundError
from mcp_os_server.command.interfaces import (
    IOutputManager,
    IProcess,
    IProcessManager,
    OutputMessageEntry,
    ProcessInfo,
    ProcessStatus,
)
from mcp_os_server.command.process_manager import ProcessManager

# Mark all tests in this module as asyncio
pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_output_manager(mocker) -> IOutputManager:
    """Fixture to create a mock IOutputManager."""
    mock = mocker.AsyncMock(spec=IOutputManager)

    async def mock_store_output(process_id: str, output_key: str, message: str | list[str]):
        # print(f"Mock storing output for {process_id}/{output_key}: {message}")
        pass

    async def mock_get_output(
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        if False: # HACK: to make it a generator
            yield

    async def mock_clear_output(process_id: str):
        pass

    mock.store_output.side_effect = mock_store_output
    mock.get_output.side_effect = mock_get_output
    mock.clear_output.side_effect = mock_clear_output

    return mock


@pytest_asyncio.fixture
async def process_manager(mock_output_manager: IOutputManager) -> AsyncGenerator[IProcessManager, None]:
    """Fixture to create a ProcessManager instance."""
    pm = ProcessManager(output_manager=mock_output_manager)
    await pm.initialize()
    yield pm
    await pm.shutdown()


@pytest_asyncio.fixture
async def process_manager_short_retention(mock_output_manager: IOutputManager) -> AsyncGenerator[IProcessManager, None]:
    """Fixture to create a ProcessManager instance with 5 seconds retention time."""
    pm = ProcessManager(output_manager=mock_output_manager, process_retention_seconds=5)
    await pm.initialize()
    yield pm
    await pm.shutdown()


async def test_start_process(process_manager: IProcessManager):
    """Test starting a simple process and check its initial state."""
    command = ["echo", "hello world"]
    process = await process_manager.start_process(
        command=command,
        directory=".",
        description="Test echo process",
        labels=["test", "echo"],
    )

    assert process is not None
    info = await process.get_details()
    assert info.command == command
    assert info.status == ProcessStatus.RUNNING
    assert info.description == "Test echo process"
    assert "test" in info.labels
    assert info.exit_code is None

    # Wait for completion and check final state
    completed_info = await process.wait_for_completion()
    assert completed_info.status == ProcessStatus.COMPLETED
    assert completed_info.exit_code == 0
    assert completed_info.end_time is not None


async def test_stop_process(process_manager: IProcessManager):
    """Test stopping a running process."""
    command = ["sleep", "10"]
    process = await process_manager.start_process(
        command=command,
        directory=".",
        description="Test sleep process",
    )

    # Ensure it's running
    info = await process.get_details()
    assert info.status == ProcessStatus.RUNNING

    # Stop the process
    await process_manager.stop_process(process.pid)

    # Check if it's terminated
    stopped_info = await process.wait_for_completion()
    assert stopped_info.status == ProcessStatus.TERMINATED
    assert stopped_info.exit_code is not None and stopped_info.exit_code != 0
    
    # On Windows, a terminated process exit code is 1. On Unix, it's -SIGTERM (-15).
    if sys.platform != "win32":
        assert stopped_info.exit_code < 0 
    else:
        assert stopped_info.exit_code > 0


async def test_wait_for_completion(process_manager: IProcessManager):
    """Test waiting for a process to complete."""
    start_time = time.monotonic()
    process = await process_manager.start_process(
        command=["sleep", "0.5"],
        directory=".",
        description="Test short sleep",
    )
    completed_info = await process.wait_for_completion()
    end_time = time.monotonic()

    assert completed_info.status == ProcessStatus.COMPLETED
    assert completed_info.exit_code == 0
    assert (end_time - start_time) >= 0.5


async def test_process_timeout(process_manager: IProcessManager):
    """Test that a process is terminated due to timeout."""
    process = await process_manager.start_process(
        command=["sleep", "10"],
        directory=".",
        description="Test timeout process",
        timeout=1,
    )

    completed_info = await process.wait_for_completion()
    assert completed_info.status == ProcessStatus.TERMINATED
    assert "timed out" in (completed_info.error_message or "").lower()


async def test_get_process_info(process_manager: IProcessManager):
    """Test getting detailed information about a process."""
    process = await process_manager.start_process(
        command=["echo", "test_info"],
        directory=".",
        description="Info Test",
    )
    info = await process_manager.get_process_info(process.pid)
    assert info.pid == process.pid
    assert info.description == "Info Test"

    with pytest.raises(ProcessNotFoundError):
        await process_manager.get_process_info("nonexistent_pid")


async def test_list_processes(process_manager: IProcessManager):
    """Test listing processes with status and label filters."""
    p1 = await process_manager.start_process(["echo", "1"], ".", "p1", labels=["group1"])
    p2 = await process_manager.start_process(["sleep", "5"], ".", "p2", labels=["group2"])

    await p1.wait_for_completion()

    all_processes = await process_manager.list_processes()
    assert len(all_processes) >= 2 # There might be other processes from other tests

    running = await process_manager.list_processes(status=ProcessStatus.RUNNING)
    assert any(p.pid == p2.pid for p in running)
    assert not any(p.pid == p1.pid for p in running)

    completed = await process_manager.list_processes(status=ProcessStatus.COMPLETED)
    assert any(p.pid == p1.pid for p in completed)
    assert not any(p.pid == p2.pid for p in completed)

    group1_procs = await process_manager.list_processes(labels=["group1"])
    assert len(group1_procs) == 1
    assert group1_procs[0].pid == p1.pid
    
    await p2.stop()


async def test_clean_processes(process_manager: IProcessManager):
    """Test cleaning up completed processes."""
    p_completed = await process_manager.start_process(["echo", "clean me"], ".", "p_completed")
    p_running = await process_manager.start_process(["sleep", "5"], ".", "p_running")

    await p_completed.wait_for_completion()

    # Attempt to clean both
    results = await process_manager.clean_processes([p_completed.pid, p_running.pid])
    
    assert results[p_completed.pid].lower() == "success"
    assert "failed" in results[p_running.pid].lower() # Cannot clean running process

    # The completed process should be gone
    with pytest.raises(ProcessNotFoundError):
        await process_manager.get_process_info(p_completed.pid)
        
    # The running process should still be there
    running_info = await process_manager.get_process_info(p_running.pid)
    assert running_info is not None

    await p_running.stop()
    await process_manager.clean_processes([p_running.pid])


async def test_command_not_found(process_manager: IProcessManager):
    """Test that starting a non-existent command raises CommandExecutionError."""
    with pytest.raises(CommandExecutionError):
        await process_manager.start_process(
            command=["non_existent_command_12345"],
            directory=".",
            description="Non-existent command",
        )


@pytest.mark.timeout(15)  # Give this test more time due to retention waiting
async def test_process_retention_seconds_auto_cleanup(process_manager_short_retention: IProcessManager):
    """Test that completed processes are automatically cleaned up after retention time."""
    # Start a short-running process
    process = await process_manager_short_retention.start_process(
        command=["echo", "retention test"],
        directory=".",
        description="Test retention cleanup",
        labels=["retention-test"],
    )
    
    # Wait for completion
    completed_info = await process.wait_for_completion()
    assert completed_info.status == ProcessStatus.COMPLETED
    
    # Process should still be available immediately after completion
    process_info = await process_manager_short_retention.get_process_info(process.pid)
    assert process_info.pid == process.pid
    
    # Process should still be in the list
    all_processes = await process_manager_short_retention.list_processes()
    assert any(p.pid == process.pid for p in all_processes)
    
    # Wait for retention time plus a small buffer (5.5 seconds)
    await asyncio.sleep(5.5)
    
    # Now the process should be automatically cleaned up
    with pytest.raises(ProcessNotFoundError):
        await process_manager_short_retention.get_process_info(process.pid)
    
    # Process should no longer be in the list
    all_processes_after = await process_manager_short_retention.list_processes()
    assert not any(p.pid == process.pid for p in all_processes_after)


@pytest.mark.timeout(15)  # Give this test more time due to retention waiting
async def test_process_retention_manual_clean_cancels_auto_cleanup(process_manager_short_retention: IProcessManager):
    """Test that manually cleaning a process cancels its automatic cleanup."""
    # Start a short-running process
    process = await process_manager_short_retention.start_process(
        command=["echo", "manual clean test"],
        directory=".",
        description="Test manual clean",
        labels=["manual-clean-test"],
    )
    
    # Wait for completion
    completed_info = await process.wait_for_completion()
    assert completed_info.status == ProcessStatus.COMPLETED
    
    # Manually clean the process before retention time expires
    results = await process_manager_short_retention.clean_processes([process.pid])
    assert results[process.pid] == "Success"
    
    # Process should be gone immediately
    with pytest.raises(ProcessNotFoundError):
        await process_manager_short_retention.get_process_info(process.pid)
    
    # Wait past the retention time to ensure auto-cleanup doesn't cause issues (5.5 seconds)
    await asyncio.sleep(5.5)
    
    # Process should still be gone (not causing any errors)
    with pytest.raises(ProcessNotFoundError):
        await process_manager_short_retention.get_process_info(process.pid)


@pytest.mark.timeout(15)  # Give this test more time due to retention waiting
async def test_process_retention_timeout_process_cleanup(process_manager_short_retention: IProcessManager):
    """Test that timed-out processes are also cleaned up after retention time."""
    # Start a process that will timeout
    process = await process_manager_short_retention.start_process(
        command=["sleep", "10"],
        directory=".",
        description="Test timeout retention",
        timeout=1,  # Very short timeout
    )
    
    # Wait for timeout and completion
    completed_info = await process.wait_for_completion()
    assert completed_info.status == ProcessStatus.TERMINATED
    assert "timed out" in (completed_info.error_message or "").lower()
    
    # Process should be in the list
    all_processes = await process_manager_short_retention.list_processes()
    assert any(p.pid == process.pid for p in all_processes)

    # Process should still be available immediately after timeout
    process_info = await process_manager_short_retention.get_process_info(process.pid)
    assert process_info.pid == process.pid
    assert process_info.status == ProcessStatus.TERMINATED

    # Wait for retention time plus buffer (5.5 seconds instead of 6)
    await asyncio.sleep(5.5)
    
    # Now the timed-out process should be automatically cleaned up
    with pytest.raises(ProcessNotFoundError):
        await process_manager_short_retention.get_process_info(process.pid)


@pytest.mark.timeout(15)  # Give this test more time due to retention waiting
async def test_process_retention_running_process_not_cleaned(process_manager_short_retention: IProcessManager):
    """Test that running processes are not cleaned up even after retention time."""
    # Start a long-running process
    process = await process_manager_short_retention.start_process(
        command=["sleep", "20"],
        directory=".",
        description="Test long running",
        labels=["long-running"],
    )
    
    # Verify it's running
    info = await process.get_details()
    assert info.status == ProcessStatus.RUNNING
    
    # Wait past the retention time (5.5 seconds)
    await asyncio.sleep(5.5)
    
    # Running process should still be available
    process_info = await process_manager_short_retention.get_process_info(process.pid)
    assert process_info.pid == process.pid
    assert process_info.status == ProcessStatus.RUNNING
    
    # Clean up
    await process.stop(force=True)
    await process.wait_for_completion() 