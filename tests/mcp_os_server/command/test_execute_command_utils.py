import os
import sys
from datetime import datetime
from typing import AsyncGenerator, Optional

import pytest

from mcp_os_server.command.exceptions import CommandExecutionError, ProcessTimeoutError
from mcp_os_server.command.execute_command_utils import execute_command
from mcp_os_server.command.interfaces import IOutputManager
from mcp_os_server.command.models import (
    CommandResult,
    OutputMessageEntry,
    ProcessStatus,
)
from mcp_os_server.command.process_manager_anyio import AnyioProcessManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_output_manager(mocker) -> IOutputManager:
    mock = mocker.Mock(spec=IOutputManager)
    mock._stored_outputs = {}

    async def _mock_store_output(
        process_id: str, output_key: str, message: str | list[str]
    ):
        if isinstance(message, str):
            messages_to_store = [message]
        else:
            messages_to_store = message
        for msg in messages_to_store:
            entry = OutputMessageEntry(
                timestamp=datetime.now(), text=msg, output_key=output_key
            )
            if process_id not in mock._stored_outputs:
                mock._stored_outputs[process_id] = []
            mock._stored_outputs[process_id].append(entry)

    async def _mock_get_output_impl(
        process_id: str,
        output_key: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        tail: Optional[int] = None,
    ) -> AsyncGenerator[OutputMessageEntry, None]:
        if process_id not in mock._stored_outputs:
            return
        all_entries = [
            entry
            for entry in mock._stored_outputs[process_id]
            if entry.output_key == output_key
        ]
        if since is not None:
            all_entries = [e for e in all_entries if e.timestamp.timestamp() >= since]
        if until is not None:
            all_entries = [e for e in all_entries if e.timestamp.timestamp() <= until]
        if tail is not None:
            all_entries = all_entries[-tail:]
        for entry in all_entries:
            yield entry

    async def _mock_clear_output(process_id: str):
        if process_id in mock._stored_outputs:
            del mock._stored_outputs[process_id]

    mock.store_output = mocker.AsyncMock(side_effect=_mock_store_output)
    mock.get_output = _mock_get_output_impl
    mock.clear_output = mocker.AsyncMock(side_effect=_mock_clear_output)
    return mock


@pytest.fixture
async def process_manager(mock_output_manager: IOutputManager):
    pm = AnyioProcessManager(mock_output_manager)
    await pm.initialize()
    yield pm
    await pm.shutdown()


async def test_execute_command_success(process_manager):
    result = await execute_command(
        process_manager=process_manager,
        command=[sys.executable, "-c", "print('hello world')"],
        directory=".",
        encoding="utf-8",
        description="Test success",
    )
    assert isinstance(result, CommandResult)
    assert result.exit_status == ProcessStatus.COMPLETED
    assert result.exit_code == 0
    assert "hello world" in result.stdout
    assert result.stderr == ""
    assert result.execution_time > 0


async def test_execute_command_timeout(process_manager):
    with pytest.raises(ProcessTimeoutError):
        await execute_command(
            process_manager=process_manager,
            command=[sys.executable, "-c", "import time; time.sleep(5)"],
            directory=".",
            encoding="utf-8",
            timeout=1,
            description="Test timeout",
        )


async def test_execute_command_with_stdin(process_manager):
    result = await execute_command(
        process_manager=process_manager,
        command=[
            sys.executable,
            os.path.join(
                "tests", "mcp_os_server", "command", "print_args_and_stdin.py"
            ),
        ],
        directory=".",
        encoding="utf-8",
        stdin_data="test input",
        description="Test stdin",
    )
    assert result.exit_status == ProcessStatus.COMPLETED
    assert result.exit_code == 0
    assert "Stdin: test input" in result.stdout


async def test_execute_command_unicode(process_manager):
    unicode_input = "中文输入"
    result = await execute_command(
        process_manager=process_manager,
        command=[
            sys.executable,
            os.path.join(
                "tests", "mcp_os_server", "command", "print_args_and_stdin.py"
            ),
        ],
        directory=".",
        encoding="utf-8",
        stdin_data=unicode_input,
        envs={"PYTHONIOENCODING": "utf-8"},
        description="Test unicode",
    )
    assert result.exit_status == ProcessStatus.COMPLETED
    assert result.exit_code == 0
    assert f"Stdin: {unicode_input}" in result.stdout


async def test_execute_command_error(process_manager):
    with pytest.raises(CommandExecutionError) as e:
        await execute_command(
            process_manager=process_manager,
            command=["non_existent_command"],
            directory=".",
            encoding="utf-8",
            description="Test error",
        )
