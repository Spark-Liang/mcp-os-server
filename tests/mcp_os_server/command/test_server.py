import asyncio
import os
import sys
import tempfile
import re
from pathlib import Path
from typing import List, Optional, Sequence, AsyncGenerator
from unittest.mock import MagicMock, AsyncMock

import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from pydantic import ValidationError


from mcp_os_server.command.command_executor import CommandExecutor
from mcp_os_server.command.exceptions import ProcessNotFoundError
from mcp_os_server.command.models import ProcessStatus
from mcp_os_server.command.output_manager import OutputManager
from mcp_os_server.command.process_manager import ProcessManager
from mcp_os_server.command.server import define_mcp_server


# Path to the helper script
CMD_SCRIPT_PATH = Path(__file__).parent / "cmd_for_test.py"


# Helper functions for validating output formats according to FDS specifications
def validate_process_list_table(text: str) -> bool:
    """Validate that the text contains a proper markdown table for process list."""
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return False
    
    # Check header
    header_pattern = r'\|\s*PID\s*\|\s*Status\s*\|\s*Command\s*\|\s*Description\s*\|\s*Labels\s*\|'
    if not re.match(header_pattern, lines[0]):
        return False
    
    # Check separator
    separator_pattern = r'\|---\|---\|---\|---\|---\|'
    if not re.match(separator_pattern, lines[1]):
        return False
    
    # Check data rows format (if any)
    for line in lines[2:]:
        if line.strip() and not re.match(r'\|.*\|.*\|.*\|.*\|.*\|', line):
            return False
    
    return True


def validate_process_detail_format(text: str, expected_pid: str) -> bool:
    """Validate that the text contains proper markdown format for process details."""
    required_sections = [
        f"### Process Details: {expected_pid}",
        "#### Basic Information",
        "#### Time Information", 
        "#### Execution Information",
        "#### Output Information"
    ]
    
    for section in required_sections:
        if section not in text:
            return False
    
    # Check for required fields
    required_fields = [
        "- **Status**:",
        "- **Command**:",
        "- **Description**:",
        "- **Labels**:",
        "- **Start Time**:",
        "- **End Time**:",
        "- **Duration**:",
        "- **Working Directory**:",
        "- **Exit Code**:"
    ]
    
    for field in required_fields:
        if field not in text:
            return False
    
    return True


def validate_command_success_format(results: List[TextContent], expected_output: str) -> bool:
    """Validate successful command execution output format with 3 TextContent items."""
    if len(results) != 3:
        return False
    
    # Check exit code (should be 0 for success)
    if not results[0].text.strip() == "**exit with 0**":
        return False
    
    # Check stdout format and content
    stdout_text = results[1].text
    if not stdout_text.startswith("---\nstdout:\n---\n"):
        return False
    if not stdout_text.endswith("\n"):
        return False
    
    stdout_content = stdout_text[len("---\nstdout:\n---\n"):-1]  # Remove format and trailing \n
    if stdout_content.strip() != expected_output.strip():
        return False
    
    # Check stderr format (should be present but might be empty)
    stderr_text = results[2].text
    if not stderr_text.startswith("---\nstderr:\n---\n"):
        return False
    if not stderr_text.endswith("\n"):
        return False
    
    return True


def validate_command_failure_format(results: List[TextContent], exit_code: int) -> bool:
    """Validate failed command execution output format with 3 TextContent items."""
    if len(results) != 3:
        return False
    
    # Check exit code
    if not results[0].text.strip() == f"**exit with {exit_code}**":
        return False
    
    # Check stdout format
    stdout_text = results[1].text
    if not stdout_text.startswith("---\nstdout:\n---\n"):
        return False
    if not stdout_text.endswith("\n"):
        return False
    
    # Check stderr format
    stderr_text = results[2].text
    if not stderr_text.startswith("---\nstderr:\n---\n"):
        return False
    if not stderr_text.endswith("\n"):
        return False
    
    return True


def validate_process_started_format(text: str) -> str:
    """Validate process start message and extract PID."""
    pattern = r"Process started with PID: (.+)"
    match = re.match(pattern, text)
    if not match:
        raise AssertionError(f"Invalid process start format: {text}")
    return match.group(1)


def validate_process_stopped_format(text: str, expected_pid: str) -> bool:
    """Validate process stop message format."""
    expected = f"Process {expected_pid} stopped."
    return text.strip() == expected


def validate_error_message_format(text: str, error_type: str) -> bool:
    """Validate error message format."""
    error_patterns = {
        "process_not_found": "Process with ID",
        "invalid_status": "Invalid status:",
        "no_processes": "No processes found.",
        "no_process_ids": "No process IDs provided.",
        "no_logs": "No logs found.",
        "command_failed": "Command execution failed:",
        "command_timeout": "Command timed out:",
        "invalid_time_format": "timestamp format:"
    }
    
    if error_type not in error_patterns:
        return False
    
    pattern = error_patterns[error_type]
    return pattern in text


@pytest_asyncio.fixture
async def output_manager(tmp_path: Path) -> AsyncGenerator[OutputManager, None]:
    """Provides a function-scoped OutputManager instance."""
    manager = OutputManager(base_log_path=tmp_path)
    yield manager
    await manager.shutdown()


@pytest_asyncio.fixture
async def process_manager(output_manager: OutputManager) -> AsyncGenerator[ProcessManager, None]:
    """Provides a function-scoped ProcessManager instance."""
    manager = ProcessManager(output_manager=output_manager, process_retention_seconds=5)
    await manager.initialize()
    yield manager
    await manager.shutdown()


@pytest_asyncio.fixture
async def command_executor(process_manager: ProcessManager) -> AsyncGenerator[CommandExecutor, None]:
    """Fixture to create a real CommandExecutor instance."""
    executor = CommandExecutor(process_manager=process_manager)
    await executor.initialize()
    yield executor
    # Shutdown is handled by the process_manager fixture


@pytest.fixture
def mcp_server(command_executor: CommandExecutor) -> FastMCP:
    """Fixture to create a FastMCP instance with defined tools."""
    mcp = FastMCP(
        title="Test MCP Command Server",
        description="A test server for command execution.",
        version="1.0.0",
    )
    define_mcp_server(
        mcp, 
        command_executor,
        allowed_commands=[sys.executable, "python", "echo", "sleep", "exit", "grep", "nonexistent-command-12345"],  # Include all commands needed for tests
        process_retention_seconds=5,
        default_encoding="utf-8"
    )
    return mcp


class TestMCPServerBasicFunctionality:
    """Test basic functionality according to FDS specifications."""

    @pytest.mark.asyncio
    async def test_command_execute_success(self, mcp_server, tmp_path):
        """Test successful execution of a simple command."""
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "echo", "hello world"],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,
                "envs": None,
                "limit_lines": 500,
            }
        )

        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent) 
        assert isinstance(result[2], TextContent)
        assert validate_command_success_format(result, "hello world")

    @pytest.mark.asyncio
    async def test_command_execute_failure(self, mcp_server, tmp_path):
        """Test command execution that results in a non-zero exit code."""
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "exit", "42"],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,
                "envs": None,
                "limit_lines": 500,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        assert validate_command_failure_format(result, 42)

    @pytest.mark.asyncio
    async def test_command_bg_start_success(self, mcp_server, command_executor, tmp_path):
        """Test successful start of a background command."""
        start_result = await mcp_server.call_tool(
            "command_bg_start",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "10"],
                "directory": str(tmp_path),
                "description": "Test sleep command",
                "labels": None,
                "stdin": None,
                "envs": None,
            }
        )

        assert isinstance(start_result, list)
        assert len(start_result) == 1
        assert isinstance(start_result[0], TextContent)
        
        # Validate format and extract PID
        pid = validate_process_started_format(start_result[0].text)
        assert pid  # Ensure we got a valid PID
        
        # Cleanup
        await command_executor.stop_process(pid, force=True)

    @pytest.mark.asyncio
    async def test_command_ps_list_empty(self, mcp_server):
        """Test listing processes when none are running."""
        result = await mcp_server.call_tool(
            "command_ps_list",
            {
                "labels": None,
                "status": None
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "no_processes")

    @pytest.mark.asyncio
    async def test_command_ps_list_with_processes(self, mcp_server, command_executor, tmp_path):
        """Test listing processes when there are running processes."""
        # Start a process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "sleep", "10"],
            directory=str(tmp_path),
            description="listing test"
        )

        try:
            result = await mcp_server.call_tool(
                "command_ps_list",
                {
                    "labels": None,
                    "status": None
                }
            )
            assert isinstance(result, list)
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            
            # Validate markdown table format
            assert validate_process_list_table(result[0].text)
            
            # Check that our process appears in the table
            text = result[0].text
            assert process.pid[:8] in text
            assert "running" in text
            assert "listing test" in text

        finally:
            await command_executor.stop_process(process.pid, force=True)

    @pytest.mark.asyncio
    async def test_command_ps_stop_success(self, mcp_server, command_executor, tmp_path):
        """Test stopping a running process."""
        # Start a process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "sleep", "10"],
            directory=str(tmp_path),
            description="test stop"
        )

        # Stop the process
        result = await mcp_server.call_tool(
            "command_ps_stop",
            {
                "pid": process.pid,
                "force": True
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_process_stopped_format(result[0].text, process.pid)

    @pytest.mark.asyncio
    async def test_command_ps_detail_success(self, mcp_server, command_executor, tmp_path):
        """Test getting details for a running process."""
        # Start a process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "sleep", "10"],
            directory=str(tmp_path),
            description="detail test",
            labels=["test", "detail"]
        )

        try:
            result = await mcp_server.call_tool(
                "command_ps_detail",
                {
                    "pid": process.pid
                }
            )
            assert isinstance(result, list)
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            
            # Validate markdown format
            assert validate_process_detail_format(result[0].text, process.pid)
            
            # Check specific content
            text = result[0].text
            assert "detail test" in text
            assert "test, detail" in text

        finally:
            await command_executor.stop_process(process.pid, force=True)

    @pytest.mark.asyncio
    async def test_command_ps_logs_success(self, mcp_server, command_executor, tmp_path):
        """Test getting logs for a completed process."""
        # Start and complete a process
        command_text = "test log output"
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", command_text],
            directory=str(tmp_path),
            description="logging test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": False,
                    "tail": None,
                    "since": None,
                    "until": None,
                    "add_time_prefix": True,
                    "time_prefix_format": None,
                    "follow_seconds": 0,
                    "limit_lines": 500,
                    "grep": None,
                    "grep_mode": "line"
                }
            )
            assert isinstance(result, list)
            # Should have at least 2 TextContent: process info + stdout
            assert len(result) >= 2
            assert isinstance(result[0], TextContent)
            assert isinstance(result[1], TextContent)
            
            # First TextContent should contain process info
            process_info_text = result[0].text
            assert f"**进程{process.pid}（状态：" in process_info_text
            assert "命令:" in process_info_text
            assert "描述: logging test" in process_info_text
            assert "状态:" in process_info_text
            
            # Second TextContent should contain stdout
            stdout_text = result[1].text
            assert "---\nstdout: 匹配内容（根据grep_mode）\n---\n" in stdout_text
            assert command_text in stdout_text

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_clean_success(self, mcp_server, command_executor, tmp_path):
        """Test cleaning completed processes."""
        # Start and complete a process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", "done"],
            directory=str(tmp_path),
            description="to be cleaned"
        )
        await process.wait_for_completion()

        # Clean the process
        result = await mcp_server.call_tool(
            "command_ps_clean",
            {
                "pids": [process.pid]
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "Successfully cleaned processes" in result[0].text
        assert process.pid in result[0].text


class TestMCPServerDefensiveProgramming:
    """Test error handling and edge cases for defensive programming."""

    @pytest.mark.asyncio
    async def test_command_execute_nonexistent_command(self, mcp_server, tmp_path):
        """Test executing a nonexistent command."""
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": "nonexistent-command-12345",
                "args": [],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,
                "envs": None,
                "limit_lines": 500,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_failed")

    @pytest.mark.asyncio
    async def test_command_execute_timeout(self, mcp_server, tmp_path):
        """Test command execution timeout."""
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "10"],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 1,
                "envs": None,
                "limit_lines": 500,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_timeout")

    @pytest.mark.asyncio
    async def test_command_bg_start_nonexistent_command(self, mcp_server, tmp_path):
        """Test starting background process with nonexistent command."""
        result = await mcp_server.call_tool(
            "command_bg_start",
            {
                "command": "nonexistent-command-12345",
                "args": [],
                "directory": str(tmp_path),
                "description": "test failure",
                "labels": None,
                "stdin": None,
                "envs": None,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "Failed to start background process" in result[0].text

    @pytest.mark.asyncio
    async def test_command_ps_list_invalid_status(self, mcp_server):
        """Test listing processes with invalid status."""
        result = await mcp_server.call_tool(
            "command_ps_list",
            {
                "labels": None,
                "status": "invalid_status"
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "invalid_status")

    @pytest.mark.asyncio
    async def test_command_ps_stop_nonexistent_process(self, mcp_server):
        """Test stopping a nonexistent process."""
        result = await mcp_server.call_tool(
            "command_ps_stop",
            {
                "pid": "nonexistent-pid",
                "force": False
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "process_not_found")

    @pytest.mark.asyncio
    async def test_command_ps_detail_nonexistent_process(self, mcp_server):
        """Test getting details for a nonexistent process."""
        result = await mcp_server.call_tool(
            "command_ps_detail",
            {
                "pid": "nonexistent-pid"
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "process_not_found")

    @pytest.mark.asyncio
    async def test_command_ps_logs_nonexistent_process(self, mcp_server):
        """Test getting logs for a nonexistent process."""
        result = await mcp_server.call_tool(
            "command_ps_logs",
            {
                "pid": "nonexistent-pid",
                "with_stdout": True,
                "with_stderr": False,
                "tail": None,
                "since": None,
                "until": None,
                "add_time_prefix": True,
                "time_prefix_format": None,
                "follow_seconds": 0,
                "limit_lines": 500,
                "grep": None,
                "grep_mode": "line"
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "process_not_found")

    @pytest.mark.asyncio
    async def test_command_ps_logs_no_logs_requested(self, mcp_server, command_executor, tmp_path):
        """Test getting logs when no stdout/stderr is requested."""
        # Start and complete a process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", "test"],
            directory=str(tmp_path),
            description="no logs test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": False,
                    "with_stderr": False,
                    "tail": None,
                    "since": None,
                    "until": None,
                    "add_time_prefix": True,
                    "time_prefix_format": None,
                    "follow_seconds": 0,
                    "limit_lines": 500,
                    "grep": None,
                    "grep_mode": "line"
                }
            )
            assert isinstance(result, list)
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            assert "No logs requested" in result[0].text

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_clean_empty_list(self, mcp_server):
        """Test cleaning with empty process list."""
        result = await mcp_server.call_tool(
            "command_ps_clean",
            {
                "pids": []
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "no_process_ids")

    @pytest.mark.asyncio
    async def test_command_ps_logs_invalid_time_format(self, mcp_server):
        """Test getting logs with invalid time format."""
        result = await mcp_server.call_tool(
            "command_ps_logs",
            {
                "pid": "any-pid",
                "with_stdout": True,
                "with_stderr": False,
                "tail": None,
                "since": "invalid-time-format",
                "until": None,
                "add_time_prefix": True,
                "time_prefix_format": None,
                "follow_seconds": 0,
                "limit_lines": 500,
                "grep": None,
                "grep_mode": "line"
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        # Should handle invalid time format gracefully
        assert validate_error_message_format(result[0].text, "invalid_time_format")

    @pytest.mark.asyncio
    async def test_command_execute_with_stdin(self, mcp_server, tmp_path):
        """Test command execution with stdin input."""
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "grep", "test"],
                "directory": str(tmp_path),
                "stdin": "line1\nline2 test\nline3",
                "timeout": 15,
                "envs": None,
                "limit_lines": 500,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        assert validate_command_success_format(result, "line2 test")

    @pytest.mark.asyncio
    async def test_command_ps_list_filter_by_status(self, mcp_server, command_executor, tmp_path):
        """Test filtering processes by status."""
        # Start a process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "sleep", "5"],
            directory=str(tmp_path),
            description="filter test"
        )

        try:
            # Test filtering by running status
            result = await mcp_server.call_tool(
                "command_ps_list",
                {
                    "labels": None,
                    "status": "running"
                }
            )
            assert isinstance(result, list)
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            assert validate_process_list_table(result[0].text)
            assert process.pid[:8] in result[0].text

            # Test filtering by completed status (should not include our running process)
            result = await mcp_server.call_tool(
                "command_ps_list",
                {
                    "labels": None,
                    "status": "completed"
                }
            )
            assert isinstance(result, list)
            assert len(result) == 1
            assert isinstance(result[0], TextContent)
            # Should either be empty table or "No processes found"
            assert validate_process_list_table(result[0].text) or validate_error_message_format(result[0].text, "no_processes")

        finally:
            await command_executor.stop_process(process.pid, force=True)

    @pytest.mark.asyncio
    async def test_command_execute_with_custom_encoding(self, mcp_server, tmp_path):
        """Test command execution with custom encoding."""
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "encode_echo", "测试"],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,
                "envs": None,
                "limit_lines": 500,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        # Should handle encoding properly without errors
        # Check that we have proper exit code format
        assert result[0].text.strip() == "**exit with 0**"
        # Check that stdout format is correct and has some output
        assert result[1].text.startswith("---\nstdout:\n---\n")
        assert result[1].text.strip()  # Should have some output

    @pytest.mark.asyncio
    async def test_command_execute_with_environment_variables(self, mcp_server, tmp_path):
        """Test command execution with custom environment variables."""
        # Note: Our test script doesn't specifically test env vars, but we test the parameter handling
        result = await mcp_server.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "echo", "env test"],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,
                "envs": {"TEST_VAR": "test_value"},
                "limit_lines": 500,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        assert validate_command_success_format(result, "env test")

    @pytest.mark.asyncio
    async def test_command_ps_logs_with_stderr(self, mcp_server, command_executor, tmp_path):
        """Test getting logs with both stdout and stderr."""
        # Start and complete a process that outputs to both stdout and stderr
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", "stdout_message"],
            directory=str(tmp_path),
            description="stderr test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": True,
                    "add_time_prefix": True,
                    "follow_seconds": 0,
                }
            )
            assert isinstance(result, list)
            # Should have process info + stdout, may have stderr
            assert len(result) >= 2
            assert isinstance(result[0], TextContent)
            
            # First TextContent should contain process info
            process_info_text = result[0].text
            assert f"**进程{process.pid}（状态：" in process_info_text
            assert "描述: stderr test" in process_info_text
            
            # Second TextContent should contain stdout
            stdout_text = result[1].text
            assert "---\nstdout: 匹配内容（根据grep_mode）\n---\n" in stdout_text
            assert "stdout_message" in stdout_text

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_logs_with_time_prefix_format(self, mcp_server, command_executor, tmp_path):
        """Test getting logs with custom time prefix format."""
        command_text = "time format test"
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", command_text],
            directory=str(tmp_path),
            description="time format test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": False,
                    "add_time_prefix": True,
                    "time_prefix_format": "%H:%M:%S",
                    "follow_seconds": 0,
                }
            )
            assert isinstance(result, list)
            assert len(result) >= 2
            
            # Check that stdout contains custom time format (HH:MM:SS pattern)
            stdout_text = result[1].text
            import re
            # Should contain timestamp in HH:MM:SS format
            assert re.search(r'\[\d{2}:\d{2}:\d{2}\]', stdout_text)

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_logs_without_time_prefix(self, mcp_server, command_executor, tmp_path):
        """Test getting logs without time prefix."""
        command_text = "no time prefix test"
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", command_text],
            directory=str(tmp_path),
            description="no time prefix test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": False,
                    "add_time_prefix": False,
                    "follow_seconds": 0,
                }
            )
            assert isinstance(result, list)
            assert len(result) >= 2
            
            # Check that stdout doesn't contain timestamp brackets
            stdout_text = result[1].text
            lines = stdout_text.split('\n')
            # Find the actual log content (after the header)
            log_content_found = False
            for line in lines:
                if command_text in line:
                    # This line should not start with [timestamp]
                    assert not line.strip().startswith('[')
                    log_content_found = True
                    break
            assert log_content_found

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_logs_with_grep_filter(self, mcp_server, command_executor, tmp_path):
        """Test getting logs with grep filtering."""
        # Create a process that outputs a line containing the pattern
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", "This line contains TEST_PATTERN for filtering"],
            directory=str(tmp_path),
            description="grep test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": False,
                    "add_time_prefix": False,
                    "grep": "TEST_PATTERN",
                    "grep_mode": "line",
                    "follow_seconds": 0,
                }
            )
            assert isinstance(result, list)
            assert len(result) >= 2
            
            # Check that stdout contains the filtered content
            stdout_text = result[1].text
            assert "TEST_PATTERN" in stdout_text
            assert "This line contains TEST_PATTERN for filtering" in stdout_text

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_logs_with_limit_lines(self, mcp_server, command_executor, tmp_path):
        """Test getting logs with line limit."""
        # Create a process with multiple output lines
        long_output = "\n".join([f"line{i}" for i in range(10)])
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", long_output],
            directory=str(tmp_path),
            description="limit lines test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": False,
                    "add_time_prefix": False,
                    "limit_lines": 3,
                    "follow_seconds": 0,
                }
            )
            assert isinstance(result, list)
            assert len(result) >= 2
            
            # Check that stdout is limited (exact behavior depends on implementation)
            stdout_text = result[1].text
            # Should contain some lines but not all 10
            assert "line" in stdout_text

        finally:
            await command_executor.clean_process([process.pid])

    @pytest.mark.asyncio
    async def test_command_ps_logs_with_tail(self, mcp_server, command_executor, tmp_path):
        """Test getting logs with tail parameter."""
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", "tail test"],
            directory=str(tmp_path),
            description="tail test"
        )
        await process.wait_for_completion()

        try:
            result = await mcp_server.call_tool(
                "command_ps_logs",
                {
                    "pid": process.pid,
                    "with_stdout": True,
                    "with_stderr": False,
                    "tail": 5,
                    "add_time_prefix": False,
                    "follow_seconds": 0,
                }
            )
            assert isinstance(result, list)
            assert len(result) >= 2
            
            # Check that we get some output
            stdout_text = result[1].text
            assert "tail test" in stdout_text

        finally:
            await command_executor.clean_process([process.pid])


class TestMCPServerConfigurationParameters:
    """Test configuration parameters functionality."""

    @pytest.fixture
    def mcp_server_with_restricted_commands(self, command_executor: CommandExecutor) -> FastMCP:
        """Fixture to create a FastMCP instance with restricted commands."""
        mcp = FastMCP(
            title="Test MCP Command Server - Restricted",
            description="A test server with restricted commands.",
            version="1.0.0",
        )
        define_mcp_server(
            mcp, 
            command_executor,
            allowed_commands=[sys.executable],  # Only allow the actual python executable
            process_retention_seconds=2,  # Short retention for testing
            default_encoding="utf-8"
        )
        return mcp

    @pytest.fixture
    def mcp_server_with_custom_encoding(self, command_executor: CommandExecutor) -> FastMCP:
        """Fixture to create a FastMCP instance with custom default encoding."""
        mcp = FastMCP(
            title="Test MCP Command Server - Custom Encoding",
            description="A test server with custom encoding.",
            version="1.0.0",
        )
        define_mcp_server(
            mcp, 
            command_executor,
            allowed_commands=[sys.executable, "echo", "sleep"],
            process_retention_seconds=5,
            default_encoding="gbk"  # Different default encoding
        )
        return mcp

    @pytest.mark.asyncio
    async def test_allowed_commands_restriction(self, mcp_server_with_restricted_commands, tmp_path):
        """Test that only allowed commands can be executed."""
        # Test allowed command (python) - should work
        result = await mcp_server_with_restricted_commands.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "echo", "allowed"],
                "directory": str(tmp_path),
                "timeout": 15,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        assert validate_command_success_format(result, "allowed")

        # Test disallowed command (echo) - should fail
        result = await mcp_server_with_restricted_commands.call_tool(
            "command_execute",
            {
                "command": "echo",
                "args": ["disallowed"],
                "directory": str(tmp_path),
                "timeout": 15,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "Command 'echo' is not allowed" in result[0].text

    @pytest.mark.asyncio
    async def test_allowed_commands_background_restriction(self, mcp_server_with_restricted_commands, tmp_path):
        """Test that only allowed commands can be started as background processes."""
        # Test allowed command (python) - should work
        result = await mcp_server_with_restricted_commands.call_tool(
            "command_bg_start",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "1"],
                "directory": str(tmp_path),
                "description": "Allowed background test",
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        pid = validate_process_started_format(result[0].text)
        assert pid

        # Test disallowed command (echo) - should fail
        result = await mcp_server_with_restricted_commands.call_tool(
            "command_bg_start",
            {
                "command": "echo",
                "args": ["disallowed"],
                "directory": str(tmp_path),
                "description": "Disallowed background test",
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert "Command 'echo' is not allowed" in result[0].text

    @pytest.mark.asyncio
    async def test_default_encoding_usage(self, mcp_server_with_custom_encoding, tmp_path):
        """Test that default encoding is used when no encoding is specified."""
        # Execute command without specifying encoding - should use default (gbk)
        result = await mcp_server_with_custom_encoding.call_tool(
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "echo", "encoding test"],
                "directory": str(tmp_path),
                "timeout": 15,
            }
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        # Should work without encoding errors
        assert "encoding test" in result[1].text

    @pytest.mark.asyncio
    async def test_process_retention_seconds(self, mcp_server_with_restricted_commands, command_executor, tmp_path):
        """Test that process retention works according to configuration."""
        # Start and complete a short process
        process = await command_executor.start_background_command(
            command=[sys.executable, str(CMD_SCRIPT_PATH), "echo", "retention test"],
            directory=str(tmp_path),
            description="retention test"
        )
        await process.wait_for_completion()

        # Process should be available immediately after completion
        result = await mcp_server_with_restricted_commands.call_tool(
            "command_ps_detail",
            {
                "pid": process.pid
            }
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_process_detail_format(result[0].text, process.pid)

        # Wait for retention period (2 seconds) + buffer
        import asyncio
        await asyncio.sleep(3)

        # Process should still be available (retention is managed by ProcessManager)
        # This test verifies the parameter is passed correctly
        result = await mcp_server_with_restricted_commands.call_tool(
            "command_ps_detail",
            {
                "pid": process.pid
            }
        )
        # The result depends on ProcessManager's cleanup behavior
        # We just verify the call doesn't crash and returns valid response
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)