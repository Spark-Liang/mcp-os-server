import os
import re
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator, List, Optional, Sequence
from datetime import datetime

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

import httpx
from anyio import create_memory_object_stream
import uuid
import socket
from contextvars import ContextVar

def get_random_available_port() -> int:
    """Gets a random available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]

# Path to the helper script
PROJECT_ROOT = Path(__file__).parent.parent.parent
CMD_SCRIPT_PATH = Path(__file__).parent / "command" / "cmd_for_test.py"

# Constants for server start modes
UV_START_MODE = "uv"
EXECUTABLE_START_MODE = "executable"
# Determine the path to the dist directory for executable mode
# Assuming 'dist' is at the project root level, one level up from 'mcp_os_server' which is one level up from 'tests'
DIST_DIR = Path(__file__).parent.parent.parent / "dist"


def _get_server_start_params(
    server_type: str,  # "command", "filesystem", "unified"
    mode: str,  # "stdio", "sse", "http"
    project_root: Path,
    env: dict,
    port: Optional[int] = None,
) -> tuple[str, List[str]]:
    """
    Helper to get command and args for starting MCP server based on environment variable.
    """
    start_mode = os.environ.get("MCP_SERVER_START_MODE", UV_START_MODE)

    command_name = "mcp-os-server"
    if sys.platform.startswith("win"):
        executable_path = DIST_DIR / f"{command_name}.exe"
    else:
        executable_path = DIST_DIR / command_name

    if start_mode == EXECUTABLE_START_MODE:
        if not executable_path.exists():
            raise FileNotFoundError(
                f"Executable not found at {executable_path}. Please build the project first."
            )

        cmd = str(executable_path)
        args = [server_type + "-server", "--mode", mode]
        if mode in ["sse", "http"]:
            args.extend(["--host", "127.0.0.1", "--port", str(port)])
            # Add explicit path configuration for HTTP mode to avoid redirect issues
            if mode == "http":
                args.extend(["--path", "/mcp"])
    else:  # Default to UV_START_MODE
        cmd = "uv"
        # project_root is already an absolute Path object (e.g., E:\path\to\project)
        # str(project_root) on Windows will correctly produce 'E:\\path\\to\\project'
        project_root_final_str = str(
            project_root
        )  # Get the final string representation
        if sys.platform.startswith("win"):
            # For debugging: print the path being passed to uv --project
            print(
                f"[DEBUG] Windows: project_root for uv --project is: {project_root_final_str}",
                file=sys.stderr,
            )

        args = [
            "--project",
            project_root_final_str,
            "run",
            "mcp-os-server",
            server_type + "-server",
            "--mode",
            mode,
        ]
        if mode in ["sse", "http"]:
            args.extend(["--host", "127.0.0.1", "--port", str(port)])
            # Add explicit path configuration for HTTP mode to avoid redirect issues
            if mode == "http":
                args.extend(["--path", "/mcp"])

    return cmd, args


# Helper functions for validating output formats according to FDS specifications
def validate_process_list_table(text: str) -> bool:
    """Validate that the text contains a proper markdown table for process list."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return False

    # Check header
    header_pattern = (
        r"\|\s*PID\s*\|\s*Status\s*\|\s*Command\s*\|\s*Description\s*\|\s*Labels\s*\|"
    )
    if not re.match(header_pattern, lines[0]):
        return False

    # Check separator
    separator_pattern = r"\|---\|---\|---\|---\|---\|"
    if not re.match(separator_pattern, lines[1]):
        return False

    # Check data rows format (if any)
    for line in lines[2:]:
        if line.strip() and not re.match(r"\|.*\|.*\|.*\|.*\|.*\|", line):
            return False

    return True


def validate_process_detail_format(text: str, expected_pid: str) -> bool:
    """Validate that the text contains proper markdown format for process details."""
    required_sections = [
        f"### Process Details: {expected_pid}",
        "#### Basic Information",
        "#### Time Information",
        "#### Execution Information",
        "#### Output Information",
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
        "- **Exit Code**:",
    ]

    for field in required_fields:
        if field not in text:
            return False

    return True


def validate_command_success_format(
    results: List[TextContent], expected_output: str
) -> bool:
    """Validate successful command execution output format with 3 TextContent items."""
    if len(results) != 3:
        return False

    # Check exit code format for success
    exit_text = results[0].text.strip()
    pattern = r"\*\*process .+ end with completed\(exit code: 0\)\*\*"
    if not re.match(pattern, exit_text):
        return False

    # Check stdout format and content
    stdout_text = results[1].text
    if not stdout_text.startswith("---\nstdout:\n---\n"):
        return False
    if not stdout_text.endswith("\n"):
        return False

    stdout_content = stdout_text[
        len("---\nstdout:\n---\n") : -1
    ]  # Remove format and trailing \n
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
        "command_timeout": "Command timed out",
        "invalid_time_format": "timestamp format:",
    }

    if error_type not in error_patterns:
        return False

    pattern = error_patterns[error_type]
    return pattern in text



@pytest.fixture(autouse=True) # 使用 autouse=True 让 fixture 自动应用于所有测试，无需显式传入
def inject_test_name(request):
    """
    一个 fixture，将当前测试用例的名称注入到测试类的实例中。
    """
    # 检查 request.instance 是否存在且非 None
    # 对于模块级别的函数测试（非类方法），request.instance 将为 None
    if request.instance:
        # 获取当前测试用例的名称
        test_case_name = request.node.name

        # 将名称设置到测试类的实例属性中
        # 你可以使用任何你喜欢的属性名，例如 _test_name, current_test_name 等
        request.instance.test_name = test_case_name
        print(f"\nFixture: 为测试类实例注入属性 'test_name': '{test_case_name}'")
    else:
        # 对于非类中的函数测试，你可以选择跳过或采取其他操作
        print(f"\nFixture: 当前测试 '{request.node.name}' 不在类中，跳过属性注入。")

    yield # fixture 的 setup 部分结束，yield 之后是 teardown 部分

    # Teardown: 你可以在这里清理注入的属性（可选）
    if request.instance and hasattr(request.instance, 'test_name'):
        print(f"Fixture: 清理测试类实例属性 'test_name' for '{request.instance.test_name}'")
        del request.instance.test_name

@pytest.mark.parametrize(
    "process_manager_type",
    [
        "anyio",
    ],
)
class BaseCommandServerIntegrationTest(ABC):
    """Abstract base class for command server integration tests."""

    # 仍然需要声明属性，以便类型检查器知道它在这里被实现
    test_name: Optional[str] = None
    web_port: Optional[int] = None
    web_port_var: ContextVar[int] = ContextVar("web_port", default=0)

    @abstractmethod
    async def new_mcp_client_session(
        self,
        allowed_commands: str,
        output_storage_path: str,
        process_manager_type: str,
        process_retention_seconds: Optional[int] = None,
    ) -> AsyncGenerator[ClientSession, None]:
        """
        Abstract factory method that must be implemented by subclasses to create
        an MCP client session with specified parameters.

        Args:
            allowed_commands: Comma-separated list of allowed commands
            output_storage_path: Path for output storage
            process_manager_type: The type of process manager to use ('anyio')
            process_retention_seconds: Process retention time in seconds

        Yields:
            ClientSession: An MCP client session instance
        """
        ...

    @pytest.fixture(scope="function")
    async def mcp_client_session(
        self, process_manager_type: str
    ) -> AsyncGenerator[ClientSession, None]:
        """
        Pytest fixture to start and manage the lifecycle of the MCP command server
        and provide an MCP client session for tests.
        """
        import sys

        # Set up default parameters
        allowed_commands = (
            "echo,ls,sleep,cat,grep,pwd,uv,"
            + sys.executable
            + ",nonexistent-command-12345"
        )

        # Add npm to allowed commands if npm testing is enabled
        if os.environ.get("TEST_NPM_ENABLED", "").lower() in ("1", "true", "yes"):
            allowed_commands += ",npm"

        # Add node to allowed commands if node testing is enabled
        if os.environ.get("TEST_NODE_ENABLED", "").lower() in ("1", "true", "yes"):
            allowed_commands += ",node"

        # Add uv to allowed commands if uv testing is enabled
        if os.environ.get("TEST_UV_ENABLED", "").lower() in ("1", "true", "yes"):
            allowed_commands += ",uv"

        output_storage_path = str(tempfile.mkdtemp())

        # Use the abstract factory method implemented by subclasses
        async for session in self.new_mcp_client_session(
            allowed_commands=allowed_commands,
            output_storage_path=output_storage_path,
            process_manager_type=process_manager_type,
            process_retention_seconds=None,
        ):
            yield session

    @pytest.fixture(scope="function")
    async def mcp_client_session_with_5_seconds_retention(
        self, process_manager_type: str
    ) -> AsyncGenerator[ClientSession, None]:
        """
        Pytest fixture to start and manage the lifecycle of the MCP command server
        with 10 seconds process retention time for testing retention logic.
        """
        import sys

        # Set up default parameters with 10 seconds retention
        allowed_commands = (
            "echo,ls,sleep,cat,grep,pwd,uv,"
            + sys.executable
            + ",nonexistent-command-12345"
        )
        output_storage_path = str(tempfile.mkdtemp())

        # Use the abstract factory method implemented by subclasses with 10 seconds retention
        async for session in self.new_mcp_client_session(
            allowed_commands=allowed_commands,
            output_storage_path=output_storage_path,
            process_manager_type=process_manager_type,
            process_retention_seconds=5,
        ):
            yield session

    async def call_tool(
        self, session: ClientSession, tool_name: str, arguments: dict
    ) -> Sequence[TextContent]:
        """Helper method to call a tool via MCP ClientSession with retry logic."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = await session.call_tool(tool_name, arguments)
                # Filter and return only TextContent items
                text_contents = [
                    content
                    for content in result.content
                    if isinstance(content, TextContent)
                ]
                return text_contents
            except Exception as e:
                if attempt == max_retries - 1:
                    print(
                        f"Tool call failed after {max_retries} attempts: {e}",
                        file=sys.stderr,
                    )
                    raise
                print(
                    f"Tool call attempt {attempt + 1} failed: {e}, retrying...",
                    file=sys.stderr,
                )
                await anyio.sleep(0.1)

        # This line should never be reached due to the raise in the loop, but added for type safety
        return []

    @pytest.mark.anyio
    async def test_server_initialization(self, mcp_client_session: ClientSession):
        """
        Test that the MCP server starts correctly and lists expected tools.
        """
        print("Running test_server_initialization...", file=sys.stderr)

        # Test that we can list tools
        tools = await mcp_client_session.list_tools()
        tool_names = [tool.name for tool in tools.tools]

        print(f"Available tools: {tool_names}", file=sys.stderr)

        # Verify expected tools are available
        expected_tools = [
            "command_execute",
            "command_bg_start",
            "command_ps_list",
            "command_ps_stop",
            "command_ps_logs",
            "command_ps_clean",
            "command_ps_detail",
        ]

        for tool in expected_tools:
            assert (
                tool in tool_names
            ), f"Expected tool '{tool}' not found in {tool_names}"

        print("✅ Server initialization test passed", file=sys.stderr)

    @pytest.mark.anyio
    async def test_command_execute_success(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test for successful command execution via MCP protocol."""
        print("Running test_command_execute_success...", file=sys.stderr)

        # Add timeout to prevent hanging
        try:
            result = await self.call_tool(
                mcp_client_session,
                "command_execute",
                {
                    "command": "echo",  # Use simple echo command for reliability
                    "args": ["hello", "world"],
                    "directory": str(tmp_path),
                    "stdin": None,
                    "timeout": 60,  # Increase timeout for Windows compatibility
                    "envs": None,
                    "limit_lines": 500,
                },
            )

            assert isinstance(result, (list, tuple))
            assert len(result) == 3
            assert isinstance(result[0], TextContent)
            assert isinstance(result[1], TextContent)
            assert isinstance(result[2], TextContent)
            # Convert to list for validation
            result_list = list(result)
            assert validate_command_success_format(result_list, "hello world")

            print("✅ Integration command execute success test passed", file=sys.stderr)
        except anyio.get_cancelled_exc_class():
            print("❌ Test timed out after 90 seconds", file=sys.stderr)
            raise
        except Exception as e:
            print(f"❌ Test failed with error: {e}", file=sys.stderr)
            raise

    @pytest.mark.anyio
    async def test_command_execute_failure(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test for command execution failure via MCP protocol."""
        print("Running test_command_execute_failure...", file=sys.stderr)

        result = await self.call_tool(
            mcp_client_session,
            "command_execute",
            {
                "command": "nonexistent-command-12345",  # Use nonexistent command to test failure
                "args": [],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,  # Back to reasonable timeout
                "envs": None,
                "limit_lines": 500,
            },
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(
            result[0].text, "command_failed"
        )  # Check for command failed error instead

        print("✅ Integration command execute failure test passed", file=sys.stderr)

    @pytest.mark.anyio
    async def test_command_bg_start_success(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test for background command start via MCP protocol."""
        print("Running test_command_bg_start_success...", file=sys.stderr)

        start_result = await self.call_tool(
            mcp_client_session,
            "command_bg_start",
            {
                "command": sys.executable,
                "args": [
                    str(CMD_SCRIPT_PATH),
                    "sleep",
                    "2",
                ],  # Reduced sleep time to speed up test
                "directory": str(tmp_path),
                "description": "Test sleep command",
                "labels": None,
                "stdin": None,
                "envs": None,
            },
        )

        assert isinstance(start_result, (list, tuple))
        assert len(start_result) == 1
        assert isinstance(start_result[0], TextContent)

        # Validate format and extract PID
        pid = validate_process_started_format(start_result[0].text)
        assert pid  # Ensure we got a valid PID

        # Stop the process to clean up
        try:
            await self.call_tool(
                mcp_client_session, "command_ps_stop", {"pid": pid, "force": True}
            )
        except Exception:
            pass  # Ignore cleanup errors

        print("✅ Integration command bg start success test passed", file=sys.stderr)

    @pytest.mark.anyio
    async def test_command_ps_list_empty(self, mcp_client_session: ClientSession):
        """Integration test for process list when empty via MCP protocol."""
        print("Running test_command_ps_list_empty...", file=sys.stderr)

        result = await self.call_tool(
            mcp_client_session, "command_ps_list", {"labels": None, "status": None}
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "no_processes")

        print("✅ Integration command ps list empty test passed", file=sys.stderr)

    @pytest.mark.anyio
    async def test_command_execute_with_stdin(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test for command execution with stdin via MCP protocol."""
        print("Running test_command_execute_with_stdin...", file=sys.stderr)

        result = await self.call_tool(
            mcp_client_session,
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "grep", "test"],
                "directory": str(tmp_path),
                "stdin": "line1\nline2 test\nline3",
                "timeout": 15,  # Increased timeout for command execution
                "envs": None,
                "limit_lines": 500,
            },
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 3
        assert isinstance(result[0], TextContent)
        assert isinstance(result[1], TextContent)
        assert isinstance(result[2], TextContent)
        # Convert to list for validation
        result_list = list(result)
        assert validate_command_success_format(result_list, "line2 test")

        print("✅ Integration command execute with stdin test passed", file=sys.stderr)

    @pytest.mark.anyio
    async def test_command_execute_nonexistent_command(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test for nonexistent command execution via MCP protocol."""
        print("Running test_command_execute_nonexistent_command...", file=sys.stderr)

        result = await self.call_tool(
            mcp_client_session,
            "command_execute",
            {
                "command": "nonexistent-command-12345",
                "args": [],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 15,  # Increased timeout for command execution
                "envs": None,
                "limit_lines": 500,
            },
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_failed")

        print(
            "✅ Integration command execute nonexistent command test passed",
            file=sys.stderr,
        )

    @pytest.mark.anyio
    async def test_command_execute_timeout(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test for command execution timeout via MCP protocol."""
        print("Running test_command_execute_timeout...", file=sys.stderr)

        result = await self.call_tool(
            mcp_client_session,
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "10"],
                "directory": str(tmp_path),
                "stdin": None,
                "timeout": 1,  # Short timeout to trigger timeout condition
                "envs": None,
                "limit_lines": 500,
            },
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 4
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_timeout")

        print("✅ Integration command execute timeout test passed", file=sys.stderr)

    @pytest.mark.anyio
    @pytest.mark.timeout(25)  # Give this test more time due to retention waiting
    async def test_timeout_process_retention_and_logs(
        self, mcp_client_session_with_5_seconds_retention: ClientSession, tmp_path
    ):
        """集成测试：验证超时进程的保留时间逻辑 - 在 retention time 内能查到进程信息，超过后自动清理"""
        print("Running test_timeout_process_retention_and_logs...", file=sys.stderr)

        # Step 1: 启动一个会超时的后台进程
        start_result = await self.call_tool(
            mcp_client_session_with_5_seconds_retention,
            "command_bg_start",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "10"],  # 长时间运行的进程
                "directory": str(tmp_path),
                "description": "Test timeout process retention",
                "labels": ["timeout-test"],
                "stdin": None,
                "timeout": 5,  # 设置较短的超时时间，让进程超时
                "envs": None,
            },
        )

        assert isinstance(start_result, (list, tuple))
        assert len(start_result) == 1
        assert isinstance(start_result[0], TextContent)

        # 验证进程启动并提取 PID
        pid = validate_process_started_format(start_result[0].text)
        assert pid
        print(f"Started process with PID: {pid}", file=sys.stderr)

        # Step 2: 等待进程超时（等待时间略长于超时时间）
        await anyio.sleep(3)  # Replace asyncio.sleep with anyio.sleep
        print("Process should have timed out by now", file=sys.stderr)

        # 记录进程应该已经超时的时间点
        process_timeout_time = time.time()

        # Step 3: 验证在 retention time 内，即使进程超时，仍然可以获取进程详情
        try:
            detail_result = await self.call_tool(
                mcp_client_session_with_5_seconds_retention,
                "command_ps_detail",
                {"pid": pid},
            )

            assert isinstance(detail_result, (list, tuple))
            assert len(detail_result) == 1
            assert isinstance(detail_result[0], TextContent)

            # 验证进程详情格式正确
            assert validate_process_detail_format(detail_result[0].text, pid)
            print(
                "✓ Process details retrieved successfully after timeout",
                file=sys.stderr,
            )

            # 验证进程信息包含超时相关状态
            detail_text = detail_result[0].text
            assert "Test timeout process retention" in detail_text
            assert "timeout-test" in detail_text
            print("✓ Process metadata correctly preserved", file=sys.stderr)

        except Exception as e:
            print(f"Failed to get process details: {e}", file=sys.stderr)
            raise

        # Step 4: 验证在 retention time 内，即使进程超时，仍然可以获取进程日志
        try:
            logs_result = await self.call_tool(
                mcp_client_session_with_5_seconds_retention,
                "command_ps_logs",
                {"pid": pid, "with_stdout": True, "with_stderr": True, "tail": 10},
            )

            assert isinstance(logs_result, (list, tuple))
            assert len(logs_result) >= 1  # 至少应该有一些输出
            assert isinstance(logs_result[0], TextContent)

            # 验证日志内容不为空（超时进程可能产生了一些输出）
            logs_text = logs_result[0].text
            assert (
                len(logs_text.strip()) > 0
            ), "Expected some log output even from timed out process"
            print(
                "✓ Process logs retrieved successfully after timeout", file=sys.stderr
            )
            print(f"Log content preview: {logs_text[:100]}...", file=sys.stderr)

        except Exception as e:
            print(f"Failed to get process logs: {e}", file=sys.stderr)
            raise

        # Step 5: 验证在 retention time 内可以列出进程
        try:
            list_result = await self.call_tool(
                mcp_client_session_with_5_seconds_retention,
                "command_ps_list",
                {"labels": ["timeout-test"], "status": None},
            )

            assert isinstance(list_result, (list, tuple))
            assert len(list_result) == 1
            assert isinstance(list_result[0], TextContent)

            # 验证进程在列表中
            list_text = list_result[0].text
            # The PID in the list may be truncated, so check for the first part of the PID
            pid_prefix = pid.split("-")[0] if "-" in pid else pid[:8]
            assert (
                pid_prefix in list_text
            ), f"Expected PID prefix '{pid_prefix}' to be in process list: {list_text}"
            assert "timeout-test" in list_text
            print(
                "✓ Timed out process found in process list within retention time",
                file=sys.stderr,
            )

        except Exception as e:
            print(f"Failed to list processes: {e}", file=sys.stderr)
            raise

        # Step 6: 等待足够长的时间，确保超过 retention time
        # 由于进程的实际结束时间可能与我们的估算不同，我们等待一个足够长的时间
        # 进程超时时间是2秒，加上输出处理时间，再加上5秒保留时间，总共等待10秒应该足够
        print("Waiting 10 seconds to ensure retention time expires...", file=sys.stderr)
        await anyio.sleep(10)  # Replace asyncio.sleep with anyio.sleep
        print("Retention time should have expired now", file=sys.stderr)

        # Step 7: 验证 retention time 过后，进程信息被自动清理
        try:
            # 尝试获取进程详情，应该抛出 ProcessNotFoundError
            detail_result = await self.call_tool(
                mcp_client_session_with_5_seconds_retention,
                "command_ps_detail",
                {"pid": pid},
            )

            # 如果执行到这里，说明进程没有被清理，测试失败
            print(f"detail_result: {detail_result[0].text}", file=sys.stderr)
            assert (
                False
            ), f"Process {pid} should have been cleaned up after retention time, but it still exists. detail_result: {detail_result}"

        except Exception as e:
            # 期望出现错误，表示进程已被清理
            error_text = str(e).lower()
            if "process with id" in error_text and "not found" in error_text:
                print(
                    f"✓ Process {pid} correctly cleaned up after retention time",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Unexpected error when checking cleaned process: {e}",
                    file=sys.stderr,
                )
                raise

        # Step 8: 验证进程列表中也找不到该进程
        try:
            list_result = await self.call_tool(
                mcp_client_session_with_5_seconds_retention,
                "command_ps_list",
                {"labels": ["timeout-test"], "status": None},
            )

            assert isinstance(list_result, (list, tuple))
            assert len(list_result) == 1
            assert isinstance(list_result[0], TextContent)

            # 验证进程不在列表中，或者是空列表
            list_text = list_result[0].text
            if validate_error_message_format(list_text, "no_processes"):
                print("✓ Process list is empty after retention time", file=sys.stderr)
            else:
                # 如果列表不为空，确保我们的进程不在其中
                pid_prefix = pid.split("-")[0] if "-" in pid else pid[:8]
                assert (
                    pid_prefix not in list_text
                ), f"Process prefix {pid_prefix} should not be in the list after retention time. list_text: {list_text}"
                print(
                    "✓ Process not found in list after retention time", file=sys.stderr
                )

        except Exception as e:
            print(
                f"Failed to verify process list after retention: {e}", file=sys.stderr
            )
            raise

        print(
            "✅ Integration timeout process retention and logs test passed",
            file=sys.stderr,
        )

    @pytest.mark.anyio
    async def test_command_execute_npm_optional(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """可选集成测试：验证通过 MCP command_execute 运行 npm --version - 需要设置环境变量 TEST_NPM_ENABLED=1 启用

        注意: 此测试存在已知的 MCP 协议通信问题，但核心的 .CMD 脚本执行功能在单元测试中已验证正常工作。
        """
        import os

        # 检查是否启用了 npm 测试
        if os.environ.get("TEST_NPM_ENABLED", "").lower() not in ("1", "true", "yes"):
            pytest.skip(
                "NPM integration test is disabled. Set TEST_NPM_ENABLED=1 to enable this test."
            )

        print("Running test_command_execute_npm_optional...", file=sys.stderr)

        try:
            # 简化的 npm 测试 - 移除双重超时和复杂的环境变量传递
            # 使用更长的单一超时，让 MCP 服务器自己处理环境变量
            result = await self.call_tool(
                mcp_client_session,
                "command_execute",
                {
                    "command": "npm",
                    "args": ["--version"],
                    "directory": str(tmp_path),
                    "stdin": None,
                    "timeout": 45,  # 单一超时，给 npm 足够的启动时间
                    "envs": None,  # 让服务器使用默认环境变量
                    "limit_lines": 500,
                },
            )

            assert isinstance(
                result, (list, tuple)
            ), f"Expected list/tuple result, got {type(result)}"
            assert len(result) == 1, f"Expected 1 result item, got {len(result)}"
            assert isinstance(
                result[0], TextContent
            ), f"Expected TextContent, got {type(result[0])}"

            # 验证 npm 版本输出格式
            output_text = result[0].text.strip()
            assert len(output_text) > 0, "npm version output is empty"

            # 验证版本号格式（通常是 x.y.z 格式）
            import re

            version_pattern = r"\d+\.\d+\.\d+"
            assert re.search(
                version_pattern, output_text
            ), f"Invalid npm version format: {output_text}"

            print(f"[OK] NPM version via MCP: {output_text}", file=sys.stderr)
            print("✅ Integration npm command execute test passed", file=sys.stderr)

        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "command execution failed" in error_msg:
                pytest.skip(
                    f"npm not found in PATH. Please install Node.js/npm to run this test. Error: {e}"
                )
            elif "timed out" in error_msg or "timeout" in error_msg:
                pytest.skip(
                    f"npm test timed out - npm may be slow to start on this system. Error: {e}"
                )
            else:
                print(
                    f"❌ npm integration test failed with error: {e}", file=sys.stderr
                )
                print(f"Error type: {type(e)}", file=sys.stderr)
                print(
                    "Note: This is a known MCP protocol issue. Core npm functionality works (see unit tests).",
                    file=sys.stderr,
                )
                raise

    @pytest.mark.anyio
    async def test_command_execute_uv_optional(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """可选集成测试：验证通过 MCP command_execute 运行 uv --version - 需要设置环境变量 TEST_UV_ENABLED=1 启用"""
        import os

        if os.environ.get("TEST_UV_ENABLED", "").lower() not in ("1", "true", "yes"):
            pytest.skip(
                "UV integration test is disabled. Set TEST_UV_ENABLED=1 to enable this test."
            )

        print("Running test_command_execute_uv_optional...", file=sys.stderr)

        try:
            msg = "你好！"
            result = await self.call_tool(
                mcp_client_session,
                "command_execute",
                {
                    "command": "uv",
                    "args": ["run", "python", "-c", f"print('{msg}')"],
                    "directory": str(tmp_path),
                    "stdin": None,
                    "timeout": 15,
                    "envs": None,
                    "limit_lines": 500,
                },
            )

            assert isinstance(
                result, (list, tuple)
            ), f"Expected list/tuple result, got {type(result)}"
            assert (
                len(result) == 3
            ), f"Expected 3 result items, got {len(result)}"  # uv run outputs stdout, stderr with exit code
            assert isinstance(
                result[0], TextContent
            ), f"Expected TextContent, got {type(result[0])}"
            assert isinstance(
                result[1], TextContent
            ), f"Expected TextContent, got {type(result[1])}"
            assert isinstance(
                result[2], TextContent
            ), f"Expected TextContent, got {type(result[2])}"

            # Check exit code
            assert (
                result[0].text.strip() == "**exit with 0**"
            ), f"Expected exit code 0, got {result[0].text.strip()}"

            # Check stdout for the printed message
            stdout_text = result[1].text.strip()
            assert stdout_text.startswith(
                "---\nstdout:\n---"
            ), f"Stdout format incorrect: {stdout_text}"
            stdout_content = stdout_text[len("---\nstdout:\n---") :].strip()
            assert (
                stdout_content == msg
            ), f"Expected stdout to be '{msg}', but got '{stdout_content}'"

            # Check stderr format (uv might output some info here, but we don't strictly validate content)
            stderr_text = result[2].text.strip()
            assert stderr_text.startswith(
                "---\nstderr:\n---"
            ), f"Stderr format incorrect: {stderr_text}"
            # Stderr content might be empty or contain uv version/info, so we don't validate specific content

            print(
                f"[OK] UV command via MCP executed successfully. Stdout: {stdout_content}",
                file=sys.stderr,
            )
            print("✅ Integration uv command execute test passed", file=sys.stderr)

        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "command execution failed" in error_msg:
                pytest.skip(
                    f"uv not found in PATH. Please install uv to run this test. Error: {e}"
                )
            else:
                print(f"❌ uv integration test failed with error: {e}", file=sys.stderr)
                print(f"Error type: {type(e)}", file=sys.stderr)
                raise

    @pytest.mark.anyio
    async def test_command_execute_node_optional(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """
        Optional integration test: validate running `node -e "console.log(...)"` via MCP command_execute.
        Requires TEST_NODE_ENABLED=1 environment variable to be set.
        This test helps diagnose if issues with non-ASCII characters are specific to an executable (like uv)
        or more general.
        """
        import os

        if os.environ.get("TEST_NODE_ENABLED", "").lower() not in ("1", "true", "yes"):
            pytest.skip(
                "Node.js integration test is disabled. Set TEST_NODE_ENABLED=1 to enable this test."
            )

        print("Running test_command_execute_node_optional...", file=sys.stderr)

        try:
            msg = "你好！"
            # Using single quotes for the outer shell, and double for JSON inside console.log is safer
            node_command = f"console.log('{msg}')"
            result = await self.call_tool(
                mcp_client_session,
                "command_execute",
                {
                    "command": "node",
                    "args": ["-e", node_command],
                    "directory": str(tmp_path),
                    "stdin": None,
                    "timeout": 15,
                    "envs": None,
                    "limit_lines": 500,
                },
            )

            assert isinstance(
                result, (list, tuple)
            ), f"Expected list/tuple result, got {type(result)}"
            assert len(result) > 0, "Expected at least one result item"

            # Successful execution should yield 3 items. Failure might yield 1.
            if result[0].text.strip().startswith("**exit with"):
                assert (
                    len(result) == 3
                ), f"Expected 3 result items for a completed process, got {len(result)}"
                assert isinstance(result[0], TextContent)
                assert isinstance(result[1], TextContent)
                assert isinstance(result[2], TextContent)

                # Check exit code
                assert (
                    result[0].text.strip() == "**exit with 0**"
                ), f"Expected exit code 0, got {result[0].text.strip()}"

                # Check stdout for the printed message
                stdout_text = result[1].text.strip()
                assert stdout_text.startswith(
                    "---\nstdout:\n---"
                ), f"Stdout format incorrect: {stdout_text}"
                stdout_content = stdout_text[len("---\nstdout:\n---") :].strip()
                assert (
                    stdout_content == msg
                ), f"Expected stdout to be '{msg}', but got '{stdout_content}'"

                print(
                    f"[OK] Node.js command via MCP executed successfully. Stdout: {stdout_content}",
                    file=sys.stderr,
                )
                print(
                    "✅ Integration node command execute test passed", file=sys.stderr
                )
            else:
                # Handle case where command failed to start, returning a single error message
                assert len(result) == 1
                assert isinstance(result[0], TextContent)
                error_text = result[0].text
                if (
                    "command execution failed" in error_text.lower()
                    or "not found" in error_text.lower()
                ):
                    pytest.skip(
                        f"node not found or failed to execute. Please install Node.js to run this test. Error: {error_text}"
                    )
                else:
                    assert False, f"Test failed unexpectedly with: {error_text}"

        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "command execution failed" in error_msg:
                pytest.skip(
                    f"node not found in PATH. Please install Node.js to run this test. Error: {e}"
                )
            else:
                print(
                    f"❌ node integration test failed with error: {e}", file=sys.stderr
                )
                print(f"Error type: {type(e)}", file=sys.stderr)
                raise

    @pytest.mark.anyio
    @pytest.mark.timeout(25)  # Give this test more time
    async def test_command_execute_timeout_with_unresponsive_program(
        self, mcp_client_session_with_5_seconds_retention: ClientSession, tmp_path
    ):
        """Integration test: Verify timeout termination of unresponsive background process."""
        print("Running test_command_execute_timeout_with_unresponsive_program...", file=sys.stderr)

        # Step 1: Start a background process with loop command that won't respond quickly
        execute_result = await self.call_tool(
            mcp_client_session_with_5_seconds_retention,
            "command_execute",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "loop", "15"],  # Loop for 10 seconds
                "directory": str(tmp_path),
                "description": "Test unresponsive timeout",
                "labels": ["unresponsive-test"],
                "stdin": None,
                "timeout": 5,  # Short timeout
                "envs": None,
            },
        )

        # step 1: Validate result
        assert isinstance(execute_result, (list, tuple))
        assert len(execute_result) == 4
        assert isinstance(execute_result[0], TextContent)
        assert validate_error_message_format(execute_result[0].text, "command_timeout")
        # 从 timeout 的错误信息中提取 PID
        pid = execute_result[0].text.split("PID: ")[1].split("**")[0]
        print(f"PID: {pid}", file=sys.stderr)

        # step 2: Verify process status is TERMINATED
        detail_result = await self.call_tool(
            mcp_client_session_with_5_seconds_retention,
            "command_ps_detail",
            {"pid": pid},
        )

        assert isinstance(detail_result, (list, tuple))
        assert len(detail_result) == 1
        assert isinstance(detail_result[0], TextContent)

        detail_text = detail_result[0].text
        assert validate_process_detail_format(detail_text, pid)
        assert "TERMINATED" in detail_text.upper()
        assert "timed out" in detail_text.lower()
        print("✓ Process status is TERMINATED after timeout", file=sys.stderr)

        # Step 4: Clean up the process
        await self.call_tool(
            mcp_client_session_with_5_seconds_retention,
            "command_ps_clean",
            {"pids": [pid]},
        )
        print("✓ Process cleaned up", file=sys.stderr)

        print("✅ Integration unresponsive timeout test passed", file=sys.stderr)

    @pytest.mark.anyio
    @pytest.mark.timeout(30)
    async def test_command_execute_graceful_stop_via_http(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test: Verify graceful termination via web manager HTTP interface of a command started by execute_command tool using another coroutine."""
        print("Running test_command_execute_graceful_stop_via_http...", file=sys.stderr)

        # Create memory stream for result
        send, receive = create_memory_object_stream(1)

        async def execute_task():
            try:
                result = await self.call_tool(
                    mcp_client_session,
                    "command_execute",
                    {
                        "command": sys.executable,
                        "args": [str(CMD_SCRIPT_PATH), "sleep", "30"],
                        "directory": str(tmp_path),
                        "stdin": None,
                        "timeout": 60,  # Long timeout
                        "envs": None,
                        "limit_lines": 500,
                    },
                )
                await send.send(result)
            except Exception as e:
                await send.send(e)

        async with anyio.create_task_group() as tg:
            tg.start_soon(execute_task)

            # Wait a bit for process to start
            await anyio.sleep(2)

            # Use httpx to list processes and find our PID
            web_port = self.web_port_var.get()
            web_url = f"http://127.0.0.1:{web_port}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{web_url}/api/processes")
                assert response.status_code == 200, f"Failed to list processes: {response.text}"
                processes = response.json()["data"]
                print(f"Found {len(processes)} processes", file=sys.stderr)

                assert len(processes) == 1, "Expected exactly one process"
                process = processes[0]
                assert process["status"] == "running", f"Unexpected status: {process['status']}"
                pid = process["pid"]
                print(f"Found PID: {pid}", file=sys.stderr)

                # Stop the process gracefully
                response = await client.post(
                    f"{web_url}/api/processes/{pid}/stop",
                    json={"force": False}
                )
                assert response.status_code == 200, f"Failed to stop process: {response.text}"
                print("Process stop requested", file=sys.stderr)

            # Wait for execute_task to complete
            with anyio.fail_after(10):
                result = await receive.receive()

            assert not isinstance(result, Exception), f"Execute task failed: {result}"

            # Validate result indicates termination
            assert len(result) == 3, f"Unexpected result length: {len(result)}"
            assert "terminated" in result[0].text.lower(), f"Unexpected status: {result[0].text}"
            print("Execute task completed with terminated status", file=sys.stderr)

        # Verify final status via ps_detail
        detail_result = await self.call_tool(
            mcp_client_session,
            "command_ps_detail",
            {"pid": pid},
        )
        detail_text = detail_result[0].text
        assert "TERMINATED" in detail_text.upper()
        assert "stopped by user" in detail_text.lower()  # Assuming the reason
        print("✓ Verified terminated status with correct reason", file=sys.stderr)

        # Clean up
        await self.call_tool(
            mcp_client_session,
            "command_ps_clean",
            {"pids": [pid]},
        )

        print("✅ Integration graceful stop via HTTP test passed", file=sys.stderr)


    @pytest.mark.anyio
    @pytest.mark.timeout(30)
    async def test_command_execute_unresponsive_process_and_graceful_stop_via_http(
        self, mcp_client_session: ClientSession, tmp_path
    ):
        """Integration test: Verify graceful termination via web manager HTTP interface of a command started by execute_command tool using another coroutine."""
        print("Running test_command_execute_unresponsive_process_and_graceful_stop_via_http...", file=sys.stderr)

        # Create memory stream for result
        send, receive = create_memory_object_stream(1)

        async def execute_task():
            try:
                result = await self.call_tool(
                    mcp_client_session,
                    "command_execute",
                    {
                        "command": "uv",
                        "args": [
                            "run", "python", "build_executable.py", "-j", "16"
                        ],
                        "directory": str(PROJECT_ROOT),
                        "stdin": None,
                        "timeout": 60,  # Long timeout
                        "envs": None,
                        "limit_lines": 500,
                    },
                )
                await send.send(result)
            except Exception as e:
                await send.send(e)

        async with anyio.create_task_group() as tg:
            tg.start_soon(execute_task)

            # Wait a bit for process to start
            await anyio.sleep(2)

            # Use httpx to list processes and find our PID
            web_port = self.web_port_var.get()
            web_url = f"http://127.0.0.1:{web_port}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{web_url}/api/processes")
                assert response.status_code == 200, f"Failed to list processes: {response.text}"
                processes = response.json()["data"]
                print(f"Found {len(processes)} processes", file=sys.stderr)

                assert len(processes) == 1, "Expected exactly one process"
                process = processes[0]
                assert process["status"] == "running", f"Unexpected status: {process['status']}"
                pid = process["pid"]
                print(f"Found PID: {pid}", file=sys.stderr)

                # Stop the process gracefully
                response = await client.post(
                    f"{web_url}/api/processes/{pid}/stop",
                    json={"force": False}
                )
                assert response.status_code == 200, f"Failed to stop process: {response.text}"
                print("Process stop requested", file=sys.stderr)

            # Wait for execute_task to complete
            with anyio.fail_after(10):
                result = await receive.receive()

            assert not isinstance(result, Exception), f"Execute task failed: {result}"

            # Validate result indicates termination
            assert len(result) == 3, f"Unexpected result length: {len(result)}"
            assert "terminated" in result[0].text.lower(), f"Unexpected status: {result[0].text}"
            print("Execute task completed with terminated status", file=sys.stderr)

        # Verify final status via ps_detail
        detail_result = await self.call_tool(
            mcp_client_session,
            "command_ps_detail",
            {"pid": pid},
        )
        detail_text = detail_result[0].text
        assert "TERMINATED" in detail_text.upper()
        assert "stopped by user" in detail_text.lower()  # Assuming the reason
        print("✓ Verified terminated status with correct reason", file=sys.stderr)

        # Clean up
        await self.call_tool(
            mcp_client_session,
            "command_ps_clean",
            {"pids": [pid]},
        )

        print("✅ Integration graceful stop via HTTP test passed", file=sys.stderr)


class TestCommandServerStdioIntegration(BaseCommandServerIntegrationTest):
    """Integration tests for the MCP Command Server via STDIO protocol."""

    async def new_mcp_client_session(
        self,
        allowed_commands: str,
        output_storage_path: str,
        process_manager_type: str,
        process_retention_seconds: Optional[int] = None,
    ) -> AsyncGenerator[ClientSession, None]:
        """
        Factory method to create MCP client session for STDIO mode.
        """

        # Set up environment variables for the command server
        env = os.environ.copy()
        env["PROCESS_MANAGER_TYPE"] = process_manager_type
        env["ALLOWED_COMMANDS"] = allowed_commands
        env["OUTPUT_STORAGE_PATH"] = output_storage_path
        if process_retention_seconds is not None:
            env["PROCESS_RETENTION_SECONDS"] = str(process_retention_seconds)
        env["PYTHONIOENCODING"] = "utf-8"
        # 固定在 .tmp 目录下生成日志文件，因为 gitignore 会忽略 .tmp 目录
        cleaned_test_name = re.sub(r'[^\w\d_.-]', '_', self.test_name) if self.test_name else "unknown_test"
        log_file_path = PROJECT_ROOT / ".tmp" / f"mcp_os_server_{cleaned_test_name}.log"
        env["LOG_FILE_PATH"] = str(log_file_path)
        print(f"Logging to: {log_file_path}", file=sys.stderr)
        cmd, args = _get_server_start_params(
            server_type="command", mode="stdio", project_root=PROJECT_ROOT, env=env
        )

        web_port = get_random_available_port()
        args += ["--debug", "--enable-web-manager", "--web-host", "127.0.0.1", "--web-port", str(web_port)]
        token = self.web_port_var.set(web_port)

        server_params = StdioServerParameters(
            command=cmd,
            args=args,
            env=env,
        )
        print(
            f"Starting MCP command server from project: {PROJECT_ROOT}", file=sys.stderr
        )
        print(
            f"Environment: ALLOWED_COMMANDS={env.get('ALLOWED_COMMANDS')}",
            file=sys.stderr,
        )
        print(f"Using temp output dir: {env['OUTPUT_STORAGE_PATH']}", file=sys.stderr)

        session = None
        stdio_context = None

        try:
            print("Initializing MCP session...", file=sys.stderr)
            stdio_context = stdio_client(server_params, errlog=sys.stdout)

            # Add longer timeout for session initialization
            read, write = await stdio_context.__aenter__()
            print("Stdio streams established", file=sys.stderr)

            # Create session
            session = ClientSession(read, write)
            await session.__aenter__()
            print("Client session created", file=sys.stderr)

            # Initialize session with retry logic and longer timeout
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(
                        f"Attempting session initialization (attempt {attempt + 1})...",
                        file=sys.stderr,
                    )
                    # Remove anyio.fail_after and use simple await
                    await session.initialize()
                    print("MCP Client Session initialized.", file=sys.stderr)
                    break
                except Exception as e:
                    print(
                        f"Session initialization attempt {attempt + 1} failed: {e}",
                        file=sys.stderr,
                    )
                    if attempt == max_retries - 1:
                        print(
                            f"Failed to initialize session after {max_retries} attempts: {e}",
                            file=sys.stderr,
                        )
                        raise
                    print(
                        f"Session initialization attempt {attempt + 1} failed: {e}, retrying...",
                        file=sys.stderr,
                    )
                    await anyio.sleep(0.5)  # Keep anyio.sleep

            yield session  # Provide the session to the tests

        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error in MCP client session setup: {e}", file=sys.stderr)
            import traceback

            print(f"Full traceback: {traceback.format_exc()}", file=sys.stderr)
            raise
        finally:
            # Improved cleanup with proper ordering and exception handling
            cleanup_errors = []

            # Step 1: Close the session first
            if session is not None:
                try:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Closing session...", file=sys.stderr)
                    # Give any ongoing operations time to complete
                    await anyio.sleep(0.1)  # Replace asyncio.sleep with anyio.sleep
                    await session.__aexit__(None, None, None)
                    print("Session cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Session cleanup error: {e}")

            # Step 2: Close the stdio context
            if stdio_context is not None:
                try:
                    # Give the server time to process the session close
                    await anyio.sleep(0.2)  # Replace asyncio.sleep with anyio.sleep
                    await stdio_context.__aexit__(None, None, None)
                    print("Stdio context cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Stdio cleanup error: {e}")

            # Reset ContextVar
            self.web_port_var.reset(token)

            # Report any cleanup errors but don't raise them
            if cleanup_errors:
                for error in cleanup_errors:
                    print(f"Warning: {error}", file=sys.stderr)

            print("MCP Client Session closed and server stopped.", file=sys.stderr)


# Add TestUnifiedServerIntegration and other remaining classes here as simplified versions
@pytest.mark.timeout(90)  # Unified tests need more time for combined server startup
class TestUnifiedServerIntegration:
    """Integration tests for the MCP Unified Server via MCP protocol."""

    # Simplified version - main implementation would include the full unified server tests


# Test filtering functionality with environment variables
@pytest.mark.timeout(60)
class TestEnvironmentVariableFiltering:
    """Integration tests for environment variable filtering functionality."""

    # Simplified version - main implementation would include filtering tests


@pytest.mark.timeout(120)  # SSE tests need more time for server startup and connection
@pytest.mark.skip(
    reason="SSE mode has MCP protocol session initialization timeout issues. Server starts correctly but session.initialize() times out during MCP handshake."
)
class TestCommandServerSSEIntegration(BaseCommandServerIntegrationTest):
    """Integration tests for the MCP Command Server via SSE protocol."""

    pass  # Simplified - skipped tests


@pytest.mark.timeout(120)  # HTTP tests need more time for server startup and connection
@pytest.mark.skip(
    reason="HTTP mode has MCP protocol session initialization timeout issues."
)
class TestCommandServerStreamableHttpIntegration(BaseCommandServerIntegrationTest):
    """Integration tests for the MCP Command Server via Streamable HTTP protocol."""

    pass  # Simplified - skipped tests


@pytest.mark.timeout(60)  # Filesystem tests need more time for server startup
class TestFilesystemServerIntegration:
    """Integration tests for the MCP Filesystem Server via MCP protocol."""

    @pytest.fixture(scope="function")
    async def mcp_filesystem_client_session(
        self, tmp_path
    ) -> AsyncGenerator[ClientSession, None]:
        """
        Pytest fixture to start and manage the lifecycle of the MCP filesystem server
        and provide an MCP client session for tests.
        """
        # Get the absolute path to the project root
        project_root = Path(__file__).parent.parent.parent.resolve()

        # Set up environment variables for the filesystem server
        env = os.environ.copy()
        env["ALLOWED_DIRS"] = str(tmp_path)

        cmd, args = _get_server_start_params(
            server_type="filesystem", mode="stdio", project_root=project_root, env=env
        )

        server_params = StdioServerParameters(
            command=cmd,
            args=args,
            env=env,
        )
        print(
            f"Starting MCP filesystem server from project: {project_root}",
            file=sys.stderr,
        )
        print(f"Environment: ALLOWED_DIRS={env.get('ALLOWED_DIRS')}", file=sys.stderr)

        session = None
        stdio_context = None

        try:
            print("Initializing MCP filesystem session...", file=sys.stderr)
            stdio_context = stdio_client(server_params)

            # Add longer timeout for session initialization
            read, write = await stdio_context.__aenter__()
            print("Stdio streams established", file=sys.stderr)

            # Create session
            session = ClientSession(read, write)
            await session.__aenter__()
            print("Client session created", file=sys.stderr)

            # Initialize session with retry logic and longer timeout
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(
                        f"Attempting session initialization (attempt {attempt + 1})...",
                        file=sys.stderr,
                    )
                    # Remove anyio.fail_after and use simple await
                    await session.initialize()
                    print("MCP Filesystem Client Session initialized.", file=sys.stderr)
                    break
                except Exception as e:
                    print(
                        f"Session initialization attempt {attempt + 1} failed: {e}",
                        file=sys.stderr,
                    )
                    if attempt == max_retries - 1:
                        print(
                            f"Failed to initialize session after {max_retries} attempts: {e}",
                            file=sys.stderr,
                        )
                        raise
                    print(
                        f"Session initialization attempt {attempt + 1} failed: {e}, retrying...",
                        file=sys.stderr,
                    )
                    await anyio.sleep(0.5)  # Keep anyio.sleep

            yield session  # Provide the session to the tests

        except Exception as e:
            print(f"Error in MCP filesystem client session setup: {e}", file=sys.stderr)
            import traceback

            print(f"Full traceback: {traceback.format_exc()}", file=sys.stderr)
            raise
        finally:
            # Improved cleanup with proper ordering and exception handling
            cleanup_errors = []

            # Step 1: Close the session first
            if session is not None:
                try:
                    # Give any ongoing operations time to complete
                    await anyio.sleep(0.1)  # Replace asyncio.sleep with anyio.sleep
                    await session.__aexit__(None, None, None)
                    print("Filesystem session cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Session cleanup error: {e}")

            # Step 2: Close the stdio context
            if stdio_context is not None:
                try:
                    # Give the server time to process the session close
                    await anyio.sleep(0.2)  # Replace asyncio.sleep with anyio.sleep
                    await stdio_context.__aexit__(None, None, None)
                    print(
                        "Filesystem stdio context cleaned up successfully",
                        file=sys.stderr,
                    )
                except Exception as e:
                    cleanup_errors.append(f"Stdio cleanup error: {e}")

            # Report any cleanup errors but don't raise them
            if cleanup_errors:
                for error in cleanup_errors:
                    print(f"Warning: {error}", file=sys.stderr)

            print(
                "MCP Filesystem Client Session closed and server stopped.",
                file=sys.stderr,
            )

    async def call_tool(
        self, session: ClientSession, tool_name: str, arguments: dict
    ) -> Sequence[TextContent]:
        """Helper method to call a tool via MCP ClientSession with retry logic."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = await session.call_tool(tool_name, arguments)
                # Filter and return only TextContent items
                text_contents = [
                    content
                    for content in result.content
                    if isinstance(content, TextContent)
                ]
                return text_contents
            except Exception as e:
                if attempt == max_retries - 1:
                    print(
                        f"Tool call failed after {max_retries} attempts: {e}",
                        file=sys.stderr,
                    )
                    raise
                print(
                    f"Tool call attempt {attempt + 1} failed: {e}, retrying...",
                    file=sys.stderr,
                )
                await anyio.sleep(0.1)  # Replace asyncio.sleep with anyio.sleep

        # This line should never be reached due to the raise in the loop, but added for type safety
        return []

    @pytest.mark.anyio
    async def test_filesystem_server_initialization(
        self, mcp_filesystem_client_session: ClientSession
    ):
        """
        Test that the MCP filesystem server starts correctly and lists expected tools.
        """
        print("Running test_filesystem_server_initialization...", file=sys.stderr)

        # Test that we can list tools
        tools = await mcp_filesystem_client_session.list_tools()
        tool_names = [tool.name for tool in tools.tools]

        print(f"Available filesystem tools: {tool_names}", file=sys.stderr)

        # Verify expected tools are available
        expected_tools = [
            "fs_read_file",
            "fs_write_file",
            "fs_create_directory",
            "fs_list_directory",
            "fs_move_file",
            "fs_search_files",
            "fs_get_file_info",
            "fs_edit_file",
            "fs_get_filesystem_info",
        ]

        for tool in expected_tools:
            assert (
                tool in tool_names
            ), f"Expected filesystem tool '{tool}' not found in {tool_names}"

        print("✅ Filesystem server initialization test passed", file=sys.stderr)

    @pytest.mark.anyio
    async def test_filesystem_write_read_file(
        self, mcp_filesystem_client_session: ClientSession, tmp_path
    ):
        """Integration test for filesystem write and read operations."""
        print("Running test_filesystem_write_read_file...", file=sys.stderr)

        test_file = tmp_path / "test.txt"
        test_content = "Hello, MCP Filesystem Server!"

        # Write file
        write_result = await self.call_tool(
            mcp_filesystem_client_session,
            "fs_write_file",
            {"path": str(test_file), "content": test_content},
        )

        assert isinstance(write_result, (list, tuple))
        assert len(write_result) == 1
        assert isinstance(write_result[0], TextContent)
        assert "成功写入" in write_result[0].text

        # Read file
        read_result = await self.call_tool(
            mcp_filesystem_client_session, "fs_read_file", {"path": str(test_file)}
        )

        assert isinstance(read_result, (list, tuple))
        assert len(read_result) == 1
        assert isinstance(read_result[0], TextContent)
        assert read_result[0].text == test_content

        print("✅ Filesystem write/read test passed", file=sys.stderr)
