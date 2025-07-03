import asyncio
from datetime import datetime
from typing import AsyncGenerator, List, Optional
from unittest.mock import MagicMock, ANY

import pytest
import pytest_asyncio
from mcp_os_server.command.command_executor import CommandExecutor
from mcp_os_server.command.interfaces import (
    ICommandExecutor,
    IProcess,
    IProcessManager,
    OutputMessageEntry,
    ProcessInfo,
    ProcessStatus,
)
from mcp_os_server.command.models import CommandResult

# Mark all tests in this module as asyncio
pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_process(mocker) -> IProcess:
    """Fixture to create a mock IProcess."""
    mock = mocker.Mock(spec=IProcess)
    mock.pid = "mock_process_123"

    async def get_details() -> ProcessInfo:
        return ProcessInfo(
            pid="mock_process_123",
            command=["mock_command"],
            directory=".",
            description="A mock process",
            status=ProcessStatus.RUNNING,
            start_time=datetime.now(),
            end_time=None,
            exit_code=None,
            timeout=None,
            error_message=None,
            labels=[],
        )

    async def wait_for_completion(timeout: Optional[int] = None) -> ProcessInfo:
        details = await get_details()
        details.status = ProcessStatus.COMPLETED
        details.exit_code = 0
        details.end_time = datetime.now()
        return details

    async def get_output(
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        if output_key == "stdout":
            yield OutputMessageEntry(timestamp=datetime.now(), text="mock stdout line", output_key="stdout")
        elif output_key == "stderr":
            yield OutputMessageEntry(timestamp=datetime.now(), text="mock stderr line", output_key="stderr")

    mock.get_details.side_effect = get_details
    mock.wait_for_completion.side_effect = wait_for_completion
    mock.get_output = get_output
    mock.stop = mocker.AsyncMock()
    mock.clean = mocker.AsyncMock(return_value="Success")
    
    return mock


@pytest.fixture
def mock_process_manager(mocker, mock_process: IProcess) -> IProcessManager:
    """Fixture to create a mock IProcessManager."""
    mock = mocker.AsyncMock(spec=IProcessManager)
    
    mock.start_process.return_value = mock_process
    mock.get_process_info.return_value = asyncio.run(mock_process.get_details())
    mock.list_processes.return_value = [asyncio.run(mock_process.get_details())]
    mock.clean_processes.return_value = {"mock_process_123": "Success"}
    
    return mock


@pytest_asyncio.fixture
async def command_executor(mock_process_manager: IProcessManager) -> ICommandExecutor:
    """Fixture to create a CommandExecutor instance."""
    executor = CommandExecutor(process_manager=mock_process_manager)
    await executor.initialize()
    return executor


async def test_execute_command(command_executor: ICommandExecutor, mock_process_manager: IProcessManager):
    """Test executing a command synchronously."""
    command = ["echo", "hello"]
    result = await command_executor.execute_command(command, ".")
    
    mock_process_manager.start_process.assert_awaited_once()
    assert isinstance(result, CommandResult)
    assert result.exit_code == 0
    assert "mock stdout line" in result.stdout


async def test_start_background_command(command_executor: ICommandExecutor, mock_process_manager: IProcessManager):
    """Test starting a background command."""
    process = await command_executor.start_background_command(["npm", "start"], ".", "test dev server")
    
    mock_process_manager.start_process.assert_awaited_once_with(
        command=["npm", "start"],
        directory=".",
        description="test dev server",
        stdin_data=None,
        timeout=None,
        envs=ANY,
        encoding=ANY,
        labels=None
    )
    assert process.pid == "mock_process_123"


async def test_get_process_logs(command_executor: ICommandExecutor, mock_process: IProcess, mock_process_manager: IProcessManager):
    """Test getting logs from a background process."""
    # This test is a bit indirect. A better test would be an integration test.
    mock_process_manager.get_process.return_value = mock_process
    
    logs = [log async for log in command_executor.get_process_logs("mock_process_123", "stdout")]
    
    mock_process_manager.get_process.assert_awaited_with("mock_process_123")
    # 不再检查 get_output 的调用，因为它现在是直接赋值的函数
    assert len(logs) == 1
    assert logs[0].text == "mock stdout line"


async def test_stop_process(command_executor: ICommandExecutor, mock_process_manager: IProcessManager):
    """Test stopping a background process."""
    await command_executor.stop_process("mock_process_123")
    mock_process_manager.stop_process.assert_awaited_once_with("mock_process_123", force=False, reason=None)


async def test_list_process(command_executor: ICommandExecutor, mock_process_manager: IProcessManager):
    """Test listing background processes."""
    processes = await command_executor.list_process()
    mock_process_manager.list_processes.assert_awaited_once()
    assert len(processes) == 1
    assert processes[0].pid == "mock_process_123"


async def test_get_process_detail(command_executor: ICommandExecutor, mock_process_manager: IProcessManager):
    """Test getting details of a background process."""
    detail = await command_executor.get_process_detail("mock_process_123")
    mock_process_manager.get_process_info.assert_awaited_once_with("mock_process_123")
    assert detail.pid == "mock_process_123"


async def test_clean_process(command_executor: ICommandExecutor, mock_process_manager: IProcessManager):
    """Test cleaning a background process."""
    result = await command_executor.clean_process(["mock_process_123"])
    mock_process_manager.clean_processes.assert_awaited_once_with(["mock_process_123"])
    assert result["mock_process_123"] == "Success" 