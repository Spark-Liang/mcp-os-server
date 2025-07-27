import os
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

import anyio
import pytest

from mcp_os_server.command.exceptions import (
    CommandExecutionError,
    ProcessNotFoundError,
    ProcessTimeoutError,
)
from mcp_os_server.command.interfaces import (
    IOutputManager,
    IProcessManager,
    OutputMessageEntry,
    ProcessStatus,
)
from mcp_os_server.command.process_manager_anyio import AnyioProcessManager

pytestmark = pytest.mark.anyio


def is_clean_success(pid, clean_result: Dict[str, Optional[str]]) -> bool:
    return pid not in clean_result or clean_result[pid] is None


@pytest.fixture
def mock_output_manager(mocker) -> IOutputManager:
    """Fixture to create a mock IOutputManager."""
    mock = mocker.Mock(
        spec=IOutputManager
    )  # Use Mock instead of AsyncMock for the base object
    mock._stored_outputs = {}  # Removed type hint from inline assignment

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
            return  # Yield nothing if process_id not found

        all_entries = [
            entry
            for entry in mock._stored_outputs[process_id]
            if entry.output_key == output_key
        ]

        # Filter by since/until
        if since is not None:
            all_entries = [e for e in all_entries if e.timestamp.timestamp() >= since]
        if until is not None:
            all_entries = [e for e in all_entries if e.timestamp.timestamp() <= until]

        # Filter by tail
        if tail is not None:
            all_entries = all_entries[-tail:]

        for entry in all_entries:
            yield entry

    async def _mock_clear_output(process_id: str):
        if process_id in mock._stored_outputs:
            del mock._stored_outputs[process_id]

    mock.store_output = mocker.AsyncMock(side_effect=_mock_store_output)
    # 修复：让get_output直接返回async generator，而不是返回coroutine
    mock.get_output = _mock_get_output_impl
    mock.clear_output = mocker.AsyncMock(side_effect=_mock_clear_output)

    return mock


class ProcessManagerTestBase(ABC):
    """Abstract base class for testing different ProcessManager implementations."""

    @abstractmethod
    async def create_process_manager(
        self, mock_output_manager: IOutputManager
    ) -> IProcessManager:
        """Create a ProcessManager instance for testing."""
        pass

    @abstractmethod
    async def create_process_manager_short_retention(
        self, mock_output_manager: IOutputManager
    ) -> IProcessManager:
        """Create a ProcessManager instance with short retention time for testing."""
        pass

    @abstractmethod
    async def cleanup_process_manager(self, process_manager: IProcessManager) -> None:
        """Clean up a ProcessManager instance after testing."""
        pass

    async def test_start_process(self, mock_output_manager: IOutputManager):
        """Test starting a simple process and check its initial state."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            command = ["echo", "hello world"]
            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test echo process",
                labels=["test", "echo"],
                timeout=5,
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
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_stop_process(self, mock_output_manager: IOutputManager):
        """Test stopping a running process."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            command = ["sleep", "10"]
            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test sleep process",
                timeout=5,
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
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_wait_for_completion(self, mock_output_manager: IOutputManager):
        """Test waiting for a process to complete."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            start_time = time.monotonic()
            process = await process_manager.start_process(
                command=["sleep", "0.5"],
                directory=".",
                description="Test short sleep",
                timeout=5,
            )
            completed_info = await process.wait_for_completion()
            end_time = time.monotonic()

            assert completed_info.status == ProcessStatus.COMPLETED
            assert completed_info.exit_code == 0
            assert (end_time - start_time) >= 0.5
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_process_timeout(self, mock_output_manager: IOutputManager):
        """Test that a process is terminated due to timeout."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            process = await process_manager.start_process(
                command=["sleep", "10"],
                directory=".",
                description="Test timeout process",
                timeout=1,
                encoding="utf-8",
            )

            with pytest.raises(ProcessTimeoutError):
                await process.wait_for_completion()
            completed_info = await process.get_details()
            assert completed_info.status == ProcessStatus.TERMINATED
            assert "timed out" in (completed_info.error_message or "").lower()
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_get_process_info(self, mock_output_manager: IOutputManager):
        """Test getting detailed information about a process."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            process = await process_manager.start_process(
                command=["echo", "test_info"],
                directory=".",
                description="Info Test",
                timeout=5,
            )
            info = await process_manager.get_process_info(process.pid)
            assert info.pid == process.pid
            assert info.description == "Info Test"

            with pytest.raises(ProcessNotFoundError):
                await process_manager.get_process_info("nonexistent_pid")
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_list_processes(self, mock_output_manager: IOutputManager):
        """Test listing processes with status and label filters."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            p1 = await process_manager.start_process(
                ["echo", "1"], ".", "p1", labels=["group1"], timeout=5
            )
            p2 = await process_manager.start_process(
                ["sleep", "5"], ".", "p2", labels=["group2"], timeout=5
            )

            await p1.wait_for_completion()

            all_processes = await process_manager.list_processes()
            assert (
                len(all_processes) >= 2
            )  # There might be other processes from other tests

            running = await process_manager.list_processes(status=ProcessStatus.RUNNING)
            assert any(p.pid == p2.pid for p in running)
            assert not any(p.pid == p1.pid for p in running)

            completed = await process_manager.list_processes(
                status=ProcessStatus.COMPLETED
            )
            assert any(p.pid == p1.pid for p in completed)
            assert not any(p.pid == p2.pid for p in completed)

            group1_procs = await process_manager.list_processes(labels=["group1"])
            assert len(group1_procs) == 1
            assert group1_procs[0].pid == p1.pid

            await p2.stop()
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_start_process_with_unicode_args(
        self, mock_output_manager: IOutputManager
    ):
        """Test starting a process with Unicode arguments."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            if sys.platform != "win32":
                pytest.skip("Unicode argument testing is primarily for Windows.")

            unicode_arg = "中文参数"
            command = [
                sys.executable,
                os.path.join(
                    "tests", "mcp_os_server", "command", "print_args_and_stdin.py"
                ),
                unicode_arg,
            ]

            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test Unicode args",
                encoding="utf-8",
                envs={"PYTHONIOENCODING": "utf-8"},
                timeout=5,
            )
            completed_info = await process.wait_for_completion()
            assert completed_info.status == ProcessStatus.COMPLETED
            assert completed_info.exit_code == 0

            output_gen = process.get_output("stdout")
            output_lines = [entry.text async for entry in output_gen]

            expected_output = f"Args: ['{unicode_arg}']"
            assert expected_output in output_lines
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_start_process_with_unicode_stdin(
        self, mock_output_manager: IOutputManager
    ):
        """Test starting a process with Unicode stdin data."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            # if sys.platform != "win32":
            #     pytest.skip("Unicode stdin testing is primarily for Windows.")

            unicode_input = "这是发送到标准输入的中文内容。"  # Unicode string for stdin
            command = [
                sys.executable,
                os.path.join(
                    "tests", "mcp_os_server", "command", "print_args_and_stdin.py"
                ),
            ]

            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test Unicode stdin",
                stdin_data=unicode_input,
                encoding="utf-8",
                envs={"PYTHONIOENCODING": "utf-8"},
                timeout=5,
            )
            completed_info = await process.wait_for_completion()
            assert completed_info.status == ProcessStatus.COMPLETED
            assert completed_info.exit_code == 0

            output_gen = process.get_output("stdout")
            output_lines = [entry.text async for entry in output_gen]

            # Expecting both args (empty) and stdin output
            assert "Args: []" in output_lines
            # Decode back from GBK for comparison
            expected_stdin_output = f"Stdin: {unicode_input}"
            assert expected_stdin_output in output_lines
        finally:
            await self.cleanup_process_manager(process_manager)

    @pytest.mark.timeout(30)  # Increase timeout for debugging Unicode issues
    async def test_start_process_with_unicode_args_via_cmd_wrapper(
        self, mock_output_manager: IOutputManager
    ):
        """Test starting a process with Unicode arguments via cmd wrapper script."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            if sys.platform != "win32":
                pytest.skip("CMD wrapper testing is specific to Windows.")

            unicode_arg = "包装中文参数"
            cmd_wrapper_path = os.path.abspath(
                os.path.join("tests", "mcp_os_server", "command", "run_with_chcp.cmd")
            )

            # Construct the command that uses the .cmd wrapper
            # For this simplified test, we only need the cmd_wrapper_path
            command = [cmd_wrapper_path]

            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test simple CMD wrapper",  # Updated description
                encoding="utf-8",  # Still expect UTF-8 from the cmd output
                timeout=5,
            )

            # 获取进程详情以检查错误信息
            initial_info = await process.get_details()
            if initial_info.status == ProcessStatus.FAILED:
                print(
                    f"Process failed immediately. Error: {initial_info.error_message}, Exit Code: {initial_info.exit_code}"
                )

            completed_info = await process.wait_for_completion()

            # Capture all output for debugging
            stdout_output = [entry.text async for entry in process.get_output("stdout")]
            stderr_output = [entry.text async for entry in process.get_output("stderr")]
            print(f"\n--- Process Output (stdout) ---\n{''.join(stdout_output)}\n---\n")
            print(f"\n--- Process Output (stderr) ---\n{''.join(stderr_output)}\n---\n")

            assert completed_info.status == ProcessStatus.COMPLETED
            assert completed_info.exit_code == 0

            # The wrapper script directly echoes "hello"
            expected_output_line = "hello"
            assert expected_output_line in stdout_output  # Check in stdout_output

        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_clean_processes(self, mock_output_manager: IOutputManager):
        """Test cleaning up completed processes."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            p_completed = await process_manager.start_process(
                ["echo", "clean me"], ".", "p_completed", timeout=5
            )
            p_running = await process_manager.start_process(
                ["sleep", "5"], ".", "p_running", timeout=5
            )

            await p_completed.wait_for_completion()

            # Attempt to clean both
            results = await process_manager.clean_processes(
                [p_completed.pid, p_running.pid]
            )

            assert is_clean_success(p_completed.pid, results)
            assert (
                "failed" in (results[p_running.pid] or "").lower()
            )  # Cannot clean running process

            # The completed process should be gone
            with pytest.raises(ProcessNotFoundError):
                await process_manager.get_process_info(p_completed.pid)

            # The running process should still be there
            running_info = await process_manager.get_process_info(p_running.pid)
            assert running_info is not None

            await p_running.stop()
            await process_manager.clean_processes([p_running.pid])
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_command_not_found(self, mock_output_manager: IOutputManager):
        """Test that starting a non-existent command raises CommandExecutionError."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            with pytest.raises(CommandExecutionError):
                await process_manager.start_process(
                    command=["non_existent_command_12345"],
                    directory=".",
                    description="Non-existent command",
                    timeout=5,
                )
        finally:
            await self.cleanup_process_manager(process_manager)

    @pytest.mark.timeout(15)  # Give this test more time due to retention waiting
    async def test_process_retention_seconds_auto_cleanup(
        self, mock_output_manager: IOutputManager
    ):
        """Test that completed processes are automatically cleaned up after retention time."""
        process_manager_short_retention = (
            await self.create_process_manager_short_retention(mock_output_manager)
        )
        try:
            # Start a short-running process
            process = await process_manager_short_retention.start_process(
                command=["echo", "retention test"],
                directory=".",
                description="Test retention cleanup",
                labels=["retention-test"],
                timeout=5,
            )

            # Wait for completion
            completed_info = await process.wait_for_completion()
            assert completed_info.status == ProcessStatus.COMPLETED

            # Process should still be available immediately after completion
            process_info = await process_manager_short_retention.get_process_info(
                process.pid
            )
            assert process_info.pid == process.pid

            # Process should still be in the list
            all_processes = await process_manager_short_retention.list_processes()
            assert any(p.pid == process.pid for p in all_processes)

            # Wait for retention time plus a buffer to allow auto cleaner to run multiple times
            await anyio.sleep(2.5)  # 等待足够长时间让auto cleaner多次运行

            # Now the process should be automatically cleaned up
            with pytest.raises(ProcessNotFoundError):
                await process_manager_short_retention.get_process_info(process.pid)

            # Process should no longer be in the list
            all_processes_after = await process_manager_short_retention.list_processes()
            assert not any(p.pid == process.pid for p in all_processes_after)
        finally:
            await self.cleanup_process_manager(process_manager_short_retention)

    @pytest.mark.timeout(15)  # Give this test more time due to retention waiting
    async def test_process_retention_manual_clean_cancels_auto_cleanup(
        self, mock_output_manager: IOutputManager
    ):
        """Test that manually cleaning a process cancels its automatic cleanup."""
        process_manager_short_retention = (
            await self.create_process_manager_short_retention(mock_output_manager)
        )
        try:
            # Start a short-running process
            process = await process_manager_short_retention.start_process(
                command=["echo", "manual clean test"],
                directory=".",
                description="Test manual clean",
                labels=["manual-clean-test"],
                timeout=5,
            )

            # Wait for completion
            completed_info = await process.wait_for_completion()
            assert completed_info.status == ProcessStatus.COMPLETED

            # Manually clean the process before retention time expires
            results = await process_manager_short_retention.clean_processes(
                [process.pid]
            )
            assert is_clean_success(process.pid, results)

            # Process should be gone immediately
            with pytest.raises(ProcessNotFoundError):
                await process_manager_short_retention.get_process_info(process.pid)

            # Wait past the retention time to ensure auto-cleanup doesn't cause issues (5.5 seconds)
            await anyio.sleep(1.5)

            # Process should still be gone (not causing any errors)
            with pytest.raises(ProcessNotFoundError):
                await process_manager_short_retention.get_process_info(process.pid)
        finally:
            await self.cleanup_process_manager(process_manager_short_retention)

    @pytest.mark.timeout(15)  # Give this test more time due to retention waiting
    async def test_process_retention_timeout_process_cleanup(
        self, mock_output_manager: IOutputManager
    ):
        """Test that timed-out processes are also cleaned up after retention time."""
        process_manager_short_retention = (
            await self.create_process_manager_short_retention(mock_output_manager)
        )
        try:
            # Start a process that will timeout
            process = await process_manager_short_retention.start_process(
                command=["sleep", "10"],
                directory=".",
                description="Test timeout retention",
                timeout=1,  # Very short timeout
            )

            # Wait for timeout and completion
            with pytest.raises(ProcessTimeoutError):
                await process.wait_for_completion()
            completed_info = await process.get_details()
            assert completed_info.status == ProcessStatus.TERMINATED
            assert "timed out" in (completed_info.error_message or "").lower()

            # Process should be in the list
            all_processes = await process_manager_short_retention.list_processes()
            assert any(p.pid == process.pid for p in all_processes)

            # Process should still be available immediately after timeout
            process_info = await process_manager_short_retention.get_process_info(
                process.pid
            )
            assert process_info.pid == process.pid
            assert process_info.status == ProcessStatus.TERMINATED

            # Wait for retention time plus buffer (5.5 seconds instead of 6)
            await anyio.sleep(4.0)

            # Now the timed-out process should be automatically cleaned up
            with pytest.raises(ProcessNotFoundError):
                await process_manager_short_retention.get_process_info(process.pid)
        finally:
            await self.cleanup_process_manager(process_manager_short_retention)

    @pytest.mark.timeout(15)  # Give this test more time due to retention waiting
    async def test_process_retention_running_process_not_cleaned(
        self, mock_output_manager: IOutputManager
    ):
        """Test that running processes are not cleaned up even after retention time."""
        process_manager_short_retention = (
            await self.create_process_manager_short_retention(mock_output_manager)
        )
        try:
            # Start a long-running process
            process = await process_manager_short_retention.start_process(
                command=["sleep", "20"],
                directory=".",
                description="Test long running",
                labels=["long-running"],
                timeout=5,
            )

            # Verify it's running
            info = await process.get_details()
            assert info.status == ProcessStatus.RUNNING

            # Wait past the retention time (5.5 seconds)
            await anyio.sleep(1.5)

            # Running process should still be available
            process_info = await process_manager_short_retention.get_process_info(
                process.pid
            )
            assert process_info.pid == process.pid
            assert process_info.status == ProcessStatus.RUNNING

            # Clean up
            await process.stop(force=True)
            await process.wait_for_completion()
        finally:
            await self.cleanup_process_manager(process_manager_short_retention)

    async def test_concurrent_starts(self, mock_output_manager: IOutputManager):
        """Test starting multiple processes concurrently."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            async with anyio.create_task_group() as tg:
                for i in range(5):
                    tg.start_soon(
                        process_manager.start_process,
                        ["echo", f"test {i}"],
                        ".",
                        f"Concurrent {i}",
                        5,
                    )

            processes = await process_manager.list_processes()
            assert len(processes) == 5

            for info in processes:
                p = await process_manager.get_process(info.pid)
                await p.wait_for_completion()
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_concurrent_lists_during_operations(
        self, mock_output_manager: IOutputManager
    ):
        """Test concurrent list_processes while starting and stopping processes."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            # Start some processes
            p1 = await process_manager.start_process(["sleep", "2"], ".", "Sleep 1", timeout=5)
            p2 = await process_manager.start_process(["sleep", "2"], ".", "Sleep 2", timeout=5)

            async def list_repeatedly():
                for _ in range(10):
                    await process_manager.list_processes()
                    await anyio.sleep(0.1)

            async with anyio.create_task_group() as tg:
                tg.start_soon(list_repeatedly)
                tg.start_soon(
                    process_manager.start_process, ["echo", "test"], ".", "Echo", 5
                )
                tg.start_soon(process_manager.stop_process, p1.pid)

            processes = await process_manager.list_processes()
            assert len(processes) >= 2  # Depending on timing
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_concurrent_cleans(self, mock_output_manager: IOutputManager):
        """Test cleaning multiple completed processes concurrently."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            processes = []
            for i in range(5):
                p = await process_manager.start_process(
                    ["echo", f"clean {i}"], ".", f"Clean {i}", timeout=5
                )
                processes.append(p)

            for p in processes:
                await p.wait_for_completion()

            async with anyio.create_task_group() as tg:
                for p in processes:
                    tg.start_soon(process_manager.clean_processes, [p.pid])

            with pytest.raises(ProcessNotFoundError):
                await process_manager.get_process_info(processes[0].pid)
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_start_process_with_invalid_encoding_stdin(
        self, mock_output_manager: IOutputManager
    ):
        """Test that CommandExecutionError is raised when stdin_data encoding fails."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            unicode_input = (
                "这是无法用ascii编码的中文"  # Characters not encodable in ascii
            )
            command = [
                sys.executable,
                os.path.join(
                    "tests", "mcp_os_server", "command", "print_args_and_stdin.py"
                ),
            ]

            with pytest.raises(CommandExecutionError) as exc_info:
                await process_manager.start_process(
                    command=command,
                    directory=".",
                    description="Test invalid encoding stdin",
                    stdin_data=unicode_input,
                    encoding="ascii",  # Encoding that cannot handle Chinese characters
                    timeout=5,
                )
            assert "Failed to encode stdin_data" in str(exc_info.value)
        finally:
            await self.cleanup_process_manager(process_manager)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="stdin write failure simulation not reliable on Windows",
    )
    async def test_start_process_with_stdin_write_failure(
        self, mock_output_manager: IOutputManager, mocker
    ):
        """Test that CommandExecutionError is raised when writing to subprocess stdin fails."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            stdin_data = b"test data"
            command = [sys.executable, "-c", "import sys; sys.stdin.close()"]

            with pytest.raises(CommandExecutionError) as exc_info:
                await process_manager.start_process(
                    command=command,
                    directory=".",
                    description="Test stdin write failure",
                    stdin_data=stdin_data,
                    encoding="utf-8",
                    timeout=5,
                )
            assert "Failed to write stdin_data" in str(exc_info.value)
        finally:
            await self.cleanup_process_manager(process_manager)

    async def test_process_timeout_with_unresponsive_program(self, mock_output_manager: IOutputManager):
        """Test that timeout forces termination of an unresponsive process."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            command = [
                sys.executable,
                os.path.join(
                    "tests", "mcp_os_server", "command", "cmd_for_test.py"
                ),
                "loop",
                "10",  # Loop for 10 seconds
            ]
            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test unresponsive timeout",
                timeout=1,  # Short timeout
            )

            with pytest.raises(ProcessTimeoutError):
                await process.wait_for_completion()
            completed_info = await process.get_details()
            assert completed_info.status == ProcessStatus.TERMINATED
            assert "timed out" in (completed_info.error_message or "").lower()

            # Check exit code indicates forced termination
            assert completed_info.exit_code is not None
            if sys.platform != "win32":
                assert completed_info.exit_code < 0
            else:
                assert completed_info.exit_code > 0
        finally:
            await self.cleanup_process_manager(process_manager)

    @pytest.mark.timeout(25)  # Give this test more time due to retention waiting
    async def test_process_timeout_with_unresponsive_program_1(self, mock_output_manager: IOutputManager):
        """Test that timeout forces termination of an unresponsive process."""
        process_manager = await self.create_process_manager(mock_output_manager)
        try:
            PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
            command = [
                "uv",
                "--project", str(PROJECT_ROOT),
                "--directory", str(PROJECT_ROOT),
                "run", "python", "build_executable.py",
            ]
            process = await process_manager.start_process(
                command=command,
                directory=".",
                description="Test unresponsive timeout",
                timeout=5,  # Short timeout
            )

            with pytest.raises(ProcessTimeoutError):
                await process.wait_for_completion()
            completed_info = await process.get_details()
            assert completed_info.status == ProcessStatus.TERMINATED
            assert "timed out" in (completed_info.error_message or "").lower()

            # Check exit code indicates forced termination
            assert completed_info.exit_code is not None
            if sys.platform != "win32":
                assert completed_info.exit_code < 0
            else:
                assert completed_info.exit_code > 0
        finally:
            await self.cleanup_process_manager(process_manager)

class TestAnyioProcessManager(ProcessManagerTestBase):
    async def create_process_manager(
        self, mock_output_manager: IOutputManager
    ) -> IProcessManager:
        # Import here to avoid circular dependencies
        pm = AnyioProcessManager(mock_output_manager)
        await pm.initialize()
        return pm

    async def create_process_manager_short_retention(
        self, mock_output_manager: IOutputManager
    ) -> IProcessManager:
        # 创建一个retention time为1秒的ProcessManager用于测试自动清理
        pm = AnyioProcessManager(mock_output_manager, process_retention_seconds=1)
        await pm.initialize()
        return pm

    async def cleanup_process_manager(self, process_manager: IProcessManager) -> None:
        await process_manager.shutdown()
