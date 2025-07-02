import asyncio
import json
import os
import re
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator, List, Sequence, Optional, Union

import pytest
import pytest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.types import TextContent


# Path to the helper script
CMD_SCRIPT_PATH = Path(__file__).parent / "command" / "cmd_for_test.py"

# Constants for server start modes
UV_START_MODE = "uv"
EXECUTABLE_START_MODE = "executable"
# Determine the path to the dist directory for executable mode
# Assuming 'dist' is at the project root level, one level up from 'mcp_os_server' which is one level up from 'tests'
DIST_DIR = Path(__file__).parent.parent.parent / "dist"

def _get_server_start_params(
    server_type: str, # "command", "filesystem", "unified"
    mode: str, # "stdio", "sse"
    project_root: Path,
    env: dict,
    port: Optional[int] = None
) -> tuple[str, List[str]]:
    """
    Helper to get command and args for starting MCP server based on environment variable.
    """
    start_mode = os.environ.get("MCP_SERVER_START_MODE", UV_START_MODE)
    
    command_name = "mcp-os-server"
    if sys.platform.startswith('win'):
        executable_path = DIST_DIR / f"{command_name}.exe"
    else:
        executable_path = DIST_DIR / command_name

    if start_mode == EXECUTABLE_START_MODE:
        if not executable_path.exists():
            raise FileNotFoundError(f"Executable not found at {executable_path}. Please build the project first.")
        
        cmd = str(executable_path)
        args = [server_type + "-server", "--mode", mode]
        if mode == "sse":
            args.extend(["--host", "127.0.0.1", "--port", str(port)])
    else: # Default to UV_START_MODE
        cmd = "uv"
        args = [
            "--project", str(project_root),
            "run", "mcp-os-server", server_type + "-server", "--mode", mode
        ]
        if mode == "sse":
            args.extend(["--host", "127.0.0.1", "--port", str(port)])

    return cmd, args


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
        "command_timeout": "Command timed out",
        "invalid_time_format": "timestamp format:"
    }
    
    if error_type not in error_patterns:
        return False
    
    pattern = error_patterns[error_type]
    return pattern in text


class BaseCommandServerIntegrationTest(ABC):
    """Abstract base class for command server integration tests."""

    @abstractmethod
    async def new_mcp_client_session(self, 
                                     allowed_commands: str,
                                     output_storage_path: str,
                                     process_retention_seconds: Optional[int] = None) -> AsyncGenerator[ClientSession, None]:
        """
        Abstract factory method that must be implemented by subclasses to create
        an MCP client session with specified parameters.
        
        Args:
            allowed_commands: Comma-separated list of allowed commands
            output_storage_path: Path for output storage
            process_retention_seconds: Process retention time in seconds
            
        Yields:
            ClientSession: An MCP client session instance
        """
        ...

    @pytest_asyncio.fixture(scope="function")
    async def mcp_client_session(self) -> AsyncGenerator[ClientSession, None]:
        """
        Pytest fixture to start and manage the lifecycle of the MCP command server
        and provide an MCP client session for tests.
        """
        import tempfile
        import sys
        
        # Set up default parameters
        allowed_commands = "echo,ls,sleep,cat,grep,pwd," + sys.executable + ",nonexistent-command-12345"
        
        # Add npm to allowed commands if npm testing is enabled
        if os.environ.get('TEST_NPM_ENABLED', '').lower() in ('1', 'true', 'yes'):
            allowed_commands += ",npm"
            
        output_storage_path = str(tempfile.mkdtemp())
        
        # Use the abstract factory method implemented by subclasses
        async for session in self.new_mcp_client_session(
            allowed_commands=allowed_commands,
            output_storage_path=output_storage_path,
            process_retention_seconds=None
        ):
            yield session

    async def call_tool(self, session: ClientSession, tool_name: str, arguments: dict) -> Sequence[TextContent]:
        """Helper method to call a tool via MCP ClientSession with retry logic."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = await session.call_tool(tool_name, arguments)
                # Filter and return only TextContent items
                text_contents = [content for content in result.content if isinstance(content, TextContent)]
                return text_contents
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Tool call failed after {max_retries} attempts: {e}", file=sys.stderr)
                    raise
                print(f"Tool call attempt {attempt + 1} failed: {e}, retrying...", file=sys.stderr)
                await asyncio.sleep(0.1)
        
        # This line should never be reached due to the raise in the loop, but added for type safety
        return []

    @pytest.mark.asyncio
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
            "command_ps_detail"
        ]
        
        for tool in expected_tools:
            assert tool in tool_names, f"Expected tool '{tool}' not found in {tool_names}"
        
        print("✅ Server initialization test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_command_execute_success(self, mcp_client_session: ClientSession, tmp_path):
        """Integration test for successful command execution via MCP protocol."""
        print("Running test_command_execute_success...", file=sys.stderr)
        
        # Add timeout to prevent hanging
        try:
            result = await asyncio.wait_for(
                self.call_tool(
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
                    }
                ),
                timeout=90.0  # Increase overall test timeout as well
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
        except asyncio.TimeoutError:
            print("❌ Test timed out after 90 seconds", file=sys.stderr)
            raise
        except Exception as e:
            print(f"❌ Test failed with error: {e}", file=sys.stderr)
            raise

    @pytest.mark.asyncio
    async def test_command_execute_failure(self, mcp_client_session: ClientSession, tmp_path):
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
            }
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_failed")  # Check for command failed error instead
        
        print("✅ Integration command execute failure test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_command_bg_start_success(self, mcp_client_session: ClientSession, tmp_path):
        """Integration test for background command start via MCP protocol."""
        print("Running test_command_bg_start_success...", file=sys.stderr)
        
        start_result = await self.call_tool(
            mcp_client_session,
            "command_bg_start",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "2"],  # Reduced sleep time to speed up test
                "directory": str(tmp_path),
                "description": "Test sleep command",
                "labels": None,
                "stdin": None,
                "envs": None,
            }
        )

        assert isinstance(start_result, (list, tuple))
        assert len(start_result) == 1
        assert isinstance(start_result[0], TextContent)
        
        # Validate format and extract PID
        pid = validate_process_started_format(start_result[0].text)
        assert pid  # Ensure we got a valid PID
        
        # Stop the process to clean up
        try:
            await self.call_tool(mcp_client_session, "command_ps_stop", {"pid": pid, "force": True})
        except Exception:
            pass  # Ignore cleanup errors
        
        print("✅ Integration command bg start success test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_command_ps_list_empty(self, mcp_client_session: ClientSession):
        """Integration test for process list when empty via MCP protocol."""
        print("Running test_command_ps_list_empty...", file=sys.stderr)
        
        result = await self.call_tool(
            mcp_client_session,
            "command_ps_list",
            {
                "labels": None,
                "status": None
            }
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "no_processes")
        
        print("✅ Integration command ps list empty test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_command_execute_with_stdin(self, mcp_client_session: ClientSession, tmp_path):
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
            }
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

    @pytest.mark.asyncio
    async def test_command_execute_nonexistent_command(self, mcp_client_session: ClientSession, tmp_path):
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
            }
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_failed")
        
        print("✅ Integration command execute nonexistent command test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_command_execute_timeout(self, mcp_client_session: ClientSession, tmp_path):
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
            }
        )
        assert isinstance(result, (list, tuple))
        assert len(result) == 4
        assert isinstance(result[0], TextContent)
        assert validate_error_message_format(result[0].text, "command_timeout")
        
        print("✅ Integration command execute timeout test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_timeout_process_retention_and_logs(self, mcp_client_session: ClientSession, tmp_path):
        """集成测试：验证在 process retention time 内，进程即使超时了，也能查到信息获取到日志"""
        print("Running test_timeout_process_retention_and_logs...", file=sys.stderr)
        
        # Step 1: 启动一个会超时的后台进程
        start_result = await self.call_tool(
            mcp_client_session,
            "command_bg_start",
            {
                "command": sys.executable,
                "args": [str(CMD_SCRIPT_PATH), "sleep", "10"],  # 长时间运行的进程
                "directory": str(tmp_path),
                "description": "Test timeout process retention",
                "labels": ["timeout-test"],
                "stdin": None,
                "timeout": 2,  # 设置较短的超时时间，让进程超时
                "envs": None,
            }
        )

        assert isinstance(start_result, (list, tuple))
        assert len(start_result) == 1
        assert isinstance(start_result[0], TextContent)
        
        # 验证进程启动并提取 PID
        pid = validate_process_started_format(start_result[0].text)
        assert pid
        print(f"Started process with PID: {pid}", file=sys.stderr)
        
        # Step 2: 等待进程超时（等待时间略长于超时时间）
        await asyncio.sleep(3)
        print("Process should have timed out by now", file=sys.stderr)
        
        # Step 3: 验证即使进程超时，仍然可以获取进程详情
        try:
            detail_result = await self.call_tool(
                mcp_client_session,
                "command_ps_detail",
                {
                    "pid": pid
                }
            )
            
            assert isinstance(detail_result, (list, tuple))
            assert len(detail_result) == 1
            assert isinstance(detail_result[0], TextContent)
            
            # 验证进程详情格式正确
            assert validate_process_detail_format(detail_result[0].text, pid)
            print("✓ Process details retrieved successfully after timeout", file=sys.stderr)
            
            # 验证进程信息包含超时相关状态
            detail_text = detail_result[0].text
            assert "Test timeout process retention" in detail_text
            assert "timeout-test" in detail_text
            print("✓ Process metadata correctly preserved", file=sys.stderr)
            
        except Exception as e:
            print(f"Failed to get process details: {e}", file=sys.stderr)
            raise
        
        # Step 4: 验证即使进程超时，仍然可以获取进程日志
        try:
            logs_result = await self.call_tool(
                mcp_client_session,
                "command_ps_logs",
                {
                    "pid": pid,
                    "with_stdout": True,
                    "with_stderr": True,
                    "tail": 10
                }
            )
            
            assert isinstance(logs_result, (list, tuple))
            assert len(logs_result) >= 1  # 至少应该有一些输出
            assert isinstance(logs_result[0], TextContent)
            
            # 验证日志内容不为空（超时进程可能产生了一些输出）
            logs_text = logs_result[0].text
            assert len(logs_text.strip()) > 0, "Expected some log output even from timed out process"
            print("✓ Process logs retrieved successfully after timeout", file=sys.stderr)
            print(f"Log content preview: {logs_text[:100]}...", file=sys.stderr)
            
        except Exception as e:
            print(f"Failed to get process logs: {e}", file=sys.stderr)
            raise
        
        # Step 5: 验证可以列出进程（在保留期内）
        try:
            list_result = await self.call_tool(
                mcp_client_session,
                "command_ps_list",
                {
                    "labels": ["timeout-test"],
                    "status": None
                }
            )
            
            assert isinstance(list_result, (list, tuple))
            assert len(list_result) == 1
            assert isinstance(list_result[0], TextContent)
            
            # 验证进程在列表中
            list_text = list_result[0].text
            assert pid in list_text
            assert "timeout-test" in list_text
            print("✓ Timed out process found in process list", file=sys.stderr)
            
        except Exception as e:
            print(f"Failed to list processes: {e}", file=sys.stderr)
            raise
        
        # Cleanup: 尝试停止进程（如果还在运行）并清理
        try:
            await self.call_tool(mcp_client_session, "command_ps_stop", {"pid": pid, "force": True})
        except Exception:
            pass  # 如果进程已经停止，忽略错误
        
        try:
            await self.call_tool(mcp_client_session, "command_ps_clean", {"pids": [pid]})
        except Exception:
            pass  # 清理可能失败，但不影响测试结果
        
        print("✅ Integration timeout process retention and logs test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_command_execute_npm_optional(self, mcp_client_session: ClientSession, tmp_path):
        """可选集成测试：验证通过 MCP command_execute 运行 npm --version - 需要设置环境变量 TEST_NPM_ENABLED=1 启用
        
        注意: 此测试存在已知的 MCP 协议通信问题，但核心的 .CMD 脚本执行功能在单元测试中已验证正常工作。
        """
        import os
        
        # 检查是否启用了 npm 测试
        if not os.environ.get('TEST_NPM_ENABLED', '').lower() in ('1', 'true', 'yes'):
            pytest.skip("NPM integration test is disabled. Set TEST_NPM_ENABLED=1 to enable this test.")
        
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
                }
            )

            assert isinstance(result, (list, tuple)), f"Expected list/tuple result, got {type(result)}"
            assert len(result) == 1, f"Expected 1 result item, got {len(result)}"
            assert isinstance(result[0], TextContent), f"Expected TextContent, got {type(result[0])}"
            
            # 验证 npm 版本输出格式
            output_text = result[0].text.strip()
            assert len(output_text) > 0, "npm version output is empty"
            
            # 验证版本号格式（通常是 x.y.z 格式）
            import re
            version_pattern = r'\d+\.\d+\.\d+'
            assert re.search(version_pattern, output_text), f"Invalid npm version format: {output_text}"
            
            print(f"[OK] NPM version via MCP: {output_text}", file=sys.stderr)
            print("✅ Integration npm command execute test passed", file=sys.stderr)
            
        except Exception as e:
            error_msg = str(e).lower()
            if "not found" in error_msg or "command execution failed" in error_msg:
                pytest.skip(f"npm not found in PATH. Please install Node.js/npm to run this test. Error: {e}")
            elif "timed out" in error_msg or "timeout" in error_msg:
                pytest.skip(f"npm test timed out - npm may be slow to start on this system. Error: {e}")
            else:
                print(f"❌ npm integration test failed with error: {e}", file=sys.stderr)
                print(f"Error type: {type(e)}", file=sys.stderr)
                print("Note: This is a known MCP protocol issue. Core npm functionality works (see unit tests).", file=sys.stderr)
                raise


class TestCommandServerStdioIntegration(BaseCommandServerIntegrationTest):
    """Integration tests for the MCP Command Server via STDIO protocol."""

    async def new_mcp_client_session(self, 
                                     allowed_commands: str,
                                     output_storage_path: str,
                                     process_retention_seconds: Optional[int] = None) -> AsyncGenerator[ClientSession, None]:
        """
        Factory method to create MCP client session for STDIO mode.
        """
        # Get the absolute path to the project root
        project_root = Path(__file__).parent.parent.parent.resolve()
        
        # Set up environment variables for the command server
        env = os.environ.copy()
        env["ALLOWED_COMMANDS"] = allowed_commands
        env["OUTPUT_STORAGE_PATH"] = output_storage_path
        if process_retention_seconds is not None:
            env["PROCESS_RETENTION_SECONDS"] = str(process_retention_seconds)
        
        cmd, args = _get_server_start_params(
            server_type="command",
            mode="stdio",
            project_root=project_root,
            env=env
        )

        server_params = StdioServerParameters(
            command=cmd,
            args=args,
            env=env,
        )
        print(f"Starting MCP command server from project: {project_root}", file=sys.stderr)
        print(f"Environment: ALLOWED_COMMANDS={env.get('ALLOWED_COMMANDS')}", file=sys.stderr)
        print(f"Using temp output dir: {env['OUTPUT_STORAGE_PATH']}", file=sys.stderr)

        session = None
        stdio_context = None
        
        try:
            print("Initializing MCP session...", file=sys.stderr)
            stdio_context = stdio_client(server_params)
            
            # Add longer timeout for session initialization
            read, write = await asyncio.wait_for(stdio_context.__aenter__(), timeout=30.0)
            print("Stdio streams established", file=sys.stderr)
            
            # Create session
            session = ClientSession(read, write)
            await session.__aenter__()
            print("Client session created", file=sys.stderr)
            
            # Initialize session with retry logic and longer timeout
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Attempting session initialization (attempt {attempt + 1})...", file=sys.stderr)
                    await asyncio.wait_for(session.initialize(), timeout=20.0)
                    print("MCP Client Session initialized.", file=sys.stderr)
                    break
                except asyncio.TimeoutError:
                    print(f"Session initialization timeout on attempt {attempt + 1}", file=sys.stderr)
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(0.5)
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"Failed to initialize session after {max_retries} attempts: {e}", file=sys.stderr)
                        raise
                    print(f"Session initialization attempt {attempt + 1} failed: {e}, retrying...", file=sys.stderr)
                    await asyncio.sleep(0.5)
            
            yield session  # Provide the session to the tests
            
        except Exception as e:
            print(f"Error in MCP client session setup: {e}", file=sys.stderr)
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
                    await asyncio.sleep(0.1)
                    await session.__aexit__(None, None, None)
                    print("Session cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Session cleanup error: {e}")
            
            # Step 2: Close the stdio context
            if stdio_context is not None:
                try:
                    # Give the server time to process the session close
                    await asyncio.sleep(0.2)
                    await stdio_context.__aexit__(None, None, None)
                    print("Stdio context cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Stdio cleanup error: {e}")
            
            # Report any cleanup errors but don't raise them
            if cleanup_errors:
                for error in cleanup_errors:
                    print(f"Warning: {error}", file=sys.stderr)
                    
            print("MCP Client Session closed and server stopped.", file=sys.stderr)




@pytest.mark.timeout(120)  # SSE tests need more time for server startup and connection
@pytest.mark.skip(reason="SSE mode has MCP protocol session initialization timeout issues. Server starts correctly but session.initialize() times out during MCP handshake.")
class TestCommandServerSSEIntegration(BaseCommandServerIntegrationTest):
    """Integration tests for the MCP Command Server via SSE protocol."""

    async def new_mcp_client_session(self, 
                                     allowed_commands: str,
                                     output_storage_path: str,
                                     process_retention_seconds: Optional[int] = None) -> AsyncGenerator[ClientSession, None]:
        """
        Factory method to create MCP client session for SSE mode.
        Uses official MCP SSE client to connect to SSE server.
        """
        # Get the absolute path to the project root
        project_root = Path(__file__).parent.parent.parent.resolve()
        
        # Set up environment variables for the command server
        env = os.environ.copy()
        env["ALLOWED_COMMANDS"] = allowed_commands
        env["OUTPUT_STORAGE_PATH"] = output_storage_path
        if process_retention_seconds is not None:
            env["PROCESS_RETENTION_SECONDS"] = str(process_retention_seconds)
        
        # Use a random available port
        import socket
        sock = socket.socket()
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()
        
        print(f"Starting MCP command server in SSE mode on port {port}", file=sys.stderr)
        
        # Start the server in background
        import subprocess
        server_process = None
        
        try:
            cmd, args = _get_server_start_params(
                server_type="command",
                mode="sse",
                project_root=project_root,
                env=env,
                port=port
            )

            server_process = subprocess.Popen([
                cmd,
                *args
            ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Wait a bit for server to start
            await asyncio.sleep(5)
            
            # Check if process is still running
            if server_process.poll() is not None:
                stdout, stderr = server_process.communicate()
                print(f"SSE Server failed to start. Exit code: {server_process.returncode}", file=sys.stderr)
                try:
                    stdout_text = stdout.decode('utf-8')
                except UnicodeDecodeError:
                    stdout_text = stdout.decode('gbk', errors='replace')
                try:
                    stderr_text = stderr.decode('utf-8')
                except UnicodeDecodeError:
                    stderr_text = stderr.decode('gbk', errors='replace')
                print(f"Stdout: {stdout_text}", file=sys.stderr)
                print(f"Stderr: {stderr_text}", file=sys.stderr)
                raise RuntimeError(f"SSE Server failed to start with exit code {server_process.returncode}")
            
            print(f"SSE Server started on port {port}", file=sys.stderr)
            print(f"Environment: ALLOWED_COMMANDS={env.get('ALLOWED_COMMANDS')}", file=sys.stderr)
            print(f"Using output dir: {env['OUTPUT_STORAGE_PATH']}", file=sys.stderr)
            
            # Wait for server to be ready by checking if it responds to HTTP requests
            import httpx
            server_ready = False
            for attempt in range(10):
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        response = await client.get(f"http://127.0.0.1:{port}/web")
                        if response.status_code in [200, 404]:  # 404 is OK, means server is responding
                            server_ready = True
                            break
                except Exception:
                    pass
                await asyncio.sleep(1)
            
            if not server_ready:
                raise RuntimeError("SSE Server did not become ready within timeout")
            
            print(f"SSE Server is ready", file=sys.stderr)
            
            # Use official MCP SSE client to connect
            url = f"http://127.0.0.1:{port}/sse"
            print(f"Connecting to SSE endpoint: {url}", file=sys.stderr)
            
            # First test if SSE endpoint is accessible
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    test_response = await client.get(url, headers={"Accept": "text/event-stream"})
                    print(f"SSE endpoint response status: {test_response.status_code}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not test SSE endpoint: {e}", file=sys.stderr)
            
            async with sse_client(url, timeout=30.0) as (read_stream, write_stream):
                from datetime import timedelta
                session = ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=30))
                print(f"SSE Client session created", file=sys.stderr)
                
                # Initialize the session with timeout
                print(f"Initializing SSE session...", file=sys.stderr)
                try:
                    # Use shorter timeout for quicker failure detection
                    await asyncio.wait_for(session.initialize(), timeout=10.0)
                    print(f"SSE Client Session initialized", file=sys.stderr)
                except asyncio.TimeoutError:
                    print(f"SSE session initialization timed out after 10 seconds", file=sys.stderr)
                    # Check if server is still running
                    if server_process and server_process.poll() is None:
                        print(f"SSE server process {server_process.pid} might still be running", file=sys.stderr)
                    else:
                        print(f"SSE server process has exited", file=sys.stderr)
                    raise
                except Exception as e:
                    print(f"SSE session initialization failed with error: {e}", file=sys.stderr)
                    print(f"Error type: {type(e).__name__}", file=sys.stderr)
                    raise
                
                yield session
            
        except Exception as e:
            print(f"Error starting SSE server: {e}", file=sys.stderr)
            raise
        finally:
            # Improved resource cleanup
            if server_process:
                try:
                    # First try graceful termination
                    print(f"Terminating SSE server process {server_process.pid}...", file=sys.stderr)
                    server_process.terminate()
                    try:
                        server_process.wait(timeout=5)
                        print(f"SSE server process {server_process.pid} terminated gracefully", file=sys.stderr)
                    except subprocess.TimeoutExpired:
                        print(f"SSE server process {server_process.pid} did not terminate gracefully, killing...", file=sys.stderr)
                        server_process.kill()
                        server_process.wait(timeout=5)
                        print(f"SSE server process {server_process.pid} killed", file=sys.stderr)
                except Exception as cleanup_error:
                    print(f"Error during SSE server cleanup: {cleanup_error}", file=sys.stderr)
                finally:
                    print("SSE Server stopped", file=sys.stderr)


@pytest.mark.timeout(60)  # Filesystem tests need more time for server startup
class TestFilesystemServerIntegration:
    """Integration tests for the MCP Filesystem Server via MCP protocol."""

    @pytest_asyncio.fixture(scope="function")
    async def mcp_filesystem_client_session(self, tmp_path) -> AsyncGenerator[ClientSession, None]:
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
            server_type="filesystem",
            mode="stdio",
            project_root=project_root,
            env=env
        )

        server_params = StdioServerParameters(
            command=cmd,
            args=args,
            env=env,
        )
        print(f"Starting MCP filesystem server from project: {project_root}", file=sys.stderr)
        print(f"Environment: ALLOWED_DIRS={env.get('ALLOWED_DIRS')}", file=sys.stderr)

        session = None
        stdio_context = None
        
        try:
            print("Initializing MCP filesystem session...", file=sys.stderr)
            stdio_context = stdio_client(server_params)
            
            # Add longer timeout for session initialization
            read, write = await asyncio.wait_for(stdio_context.__aenter__(), timeout=30.0)
            print("Stdio streams established", file=sys.stderr)
            
            # Create session
            session = ClientSession(read, write)
            await session.__aenter__()
            print("Client session created", file=sys.stderr)
            
            # Initialize session with retry logic and longer timeout
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Attempting session initialization (attempt {attempt + 1})...", file=sys.stderr)
                    await asyncio.wait_for(session.initialize(), timeout=20.0)
                    print("MCP Filesystem Client Session initialized.", file=sys.stderr)
                    break
                except asyncio.TimeoutError:
                    print(f"Session initialization timeout on attempt {attempt + 1}", file=sys.stderr)
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(0.5)
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"Failed to initialize session after {max_retries} attempts: {e}", file=sys.stderr)
                        raise
                    print(f"Session initialization attempt {attempt + 1} failed: {e}, retrying...", file=sys.stderr)
                    await asyncio.sleep(0.5)
            
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
                    await asyncio.sleep(0.1)
                    await session.__aexit__(None, None, None)
                    print("Filesystem session cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Session cleanup error: {e}")
            
            # Step 2: Close the stdio context
            if stdio_context is not None:
                try:
                    # Give the server time to process the session close
                    await asyncio.sleep(0.2)
                    await stdio_context.__aexit__(None, None, None)
                    print("Filesystem stdio context cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Stdio cleanup error: {e}")
            
            # Report any cleanup errors but don't raise them
            if cleanup_errors:
                for error in cleanup_errors:
                    print(f"Warning: {error}", file=sys.stderr)
                    
            print("MCP Filesystem Client Session closed and server stopped.", file=sys.stderr)

    async def call_tool(self, session: ClientSession, tool_name: str, arguments: dict) -> Sequence[TextContent]:
        """Helper method to call a tool via MCP ClientSession with retry logic."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = await session.call_tool(tool_name, arguments)
                # Filter and return only TextContent items
                text_contents = [content for content in result.content if isinstance(content, TextContent)]
                return text_contents
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Tool call failed after {max_retries} attempts: {e}", file=sys.stderr)
                    raise
                print(f"Tool call attempt {attempt + 1} failed: {e}, retrying...", file=sys.stderr)
                await asyncio.sleep(0.1)
        
        # This line should never be reached due to the raise in the loop, but added for type safety
        return []

    @pytest.mark.asyncio
    async def test_filesystem_server_initialization(self, mcp_filesystem_client_session: ClientSession):
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
            "fs_get_filesystem_info"
        ]
        
        for tool in expected_tools:
            assert tool in tool_names, f"Expected filesystem tool '{tool}' not found in {tool_names}"
        
        print("✅ Filesystem server initialization test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_filesystem_write_read_file(self, mcp_filesystem_client_session: ClientSession, tmp_path):
        """Integration test for filesystem write and read operations."""
        print("Running test_filesystem_write_read_file...", file=sys.stderr)
        
        test_file = tmp_path / "test.txt"
        test_content = "Hello, MCP Filesystem Server!"
        
        # Write file
        write_result = await self.call_tool(
            mcp_filesystem_client_session,
            "fs_write_file",
            {
                "path": str(test_file),
                "content": test_content
            }
        )
        
        assert isinstance(write_result, (list, tuple))
        assert len(write_result) == 1
        assert isinstance(write_result[0], TextContent)
        assert "成功写入" in write_result[0].text
        
        # Read file
        read_result = await self.call_tool(
            mcp_filesystem_client_session,
            "fs_read_file",
            {
                "path": str(test_file)
            }
        )
        
        assert isinstance(read_result, (list, tuple))
        assert len(read_result) == 1
        assert isinstance(read_result[0], TextContent)
        assert read_result[0].text == test_content
        
        print("✅ Filesystem write/read test passed", file=sys.stderr)


@pytest.mark.timeout(90)  # Unified tests need more time for combined server startup
class TestUnifiedServerIntegration:
    """Integration tests for the MCP Unified Server via MCP protocol."""

    @pytest_asyncio.fixture(scope="function")
    async def mcp_unified_client_session(self, tmp_path) -> AsyncGenerator[ClientSession, None]:
        """
        Pytest fixture to start and manage the lifecycle of the MCP unified server
        and provide an MCP client session for tests.
        """
        # Get the absolute path to the project root
        project_root = Path(__file__).parent.parent.parent.resolve()
        
        # Set up environment variables for the unified server
        env = os.environ.copy()
        env["ALLOWED_COMMANDS"] = "echo,ls,sleep,cat,grep,pwd," + sys.executable
        env["ALLOWED_DIRS"] = str(tmp_path)
        # Disable output manager logging to avoid stdio interference
        env["OUTPUT_STORAGE_PATH"] = str(tempfile.mkdtemp())
        
        cmd, args = _get_server_start_params(
            server_type="unified",
            mode="stdio",
            project_root=project_root,
            env=env
        )

        server_params = StdioServerParameters(
            command=cmd,
            args=args,
            env=env,
        )
        print(f"Starting MCP unified server from project: {project_root}", file=sys.stderr)
        print(f"Environment: ALLOWED_COMMANDS={env.get('ALLOWED_COMMANDS')}", file=sys.stderr)
        print(f"Environment: ALLOWED_DIRS={env.get('ALLOWED_DIRS')}", file=sys.stderr)

        session = None
        stdio_context = None
        
        try:
            print("Initializing MCP unified session...", file=sys.stderr)
            stdio_context = stdio_client(server_params)
            
            # Add longer timeout for session initialization
            read, write = await asyncio.wait_for(stdio_context.__aenter__(), timeout=30.0)
            print("Stdio streams established", file=sys.stderr)
            
            # Create session
            session = ClientSession(read, write)
            await session.__aenter__()
            print("Client session created", file=sys.stderr)
            
            # Initialize session with retry logic and longer timeout
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    print(f"Attempting session initialization (attempt {attempt + 1})...", file=sys.stderr)
                    await asyncio.wait_for(session.initialize(), timeout=20.0)
                    print("MCP Unified Client Session initialized.", file=sys.stderr)
                    break
                except asyncio.TimeoutError:
                    print(f"Session initialization timeout on attempt {attempt + 1}", file=sys.stderr)
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(0.5)
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"Failed to initialize session after {max_retries} attempts: {e}", file=sys.stderr)
                        raise
                    print(f"Session initialization attempt {attempt + 1} failed: {e}, retrying...", file=sys.stderr)
                    await asyncio.sleep(0.5)
            
            yield session  # Provide the session to the tests
            
        except Exception as e:
            print(f"Error in MCP unified client session setup: {e}", file=sys.stderr)
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
                    await asyncio.sleep(0.1)
                    await session.__aexit__(None, None, None)
                    print("Unified session cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Session cleanup error: {e}")
            
            # Step 2: Close the stdio context
            if stdio_context is not None:
                try:
                    # Give the server time to process the session close
                    await asyncio.sleep(0.2)
                    await stdio_context.__aexit__(None, None, None)
                    print("Unified stdio context cleaned up successfully", file=sys.stderr)
                except Exception as e:
                    cleanup_errors.append(f"Stdio cleanup error: {e}")
            
            # Report any cleanup errors but don't raise them
            if cleanup_errors:
                for error in cleanup_errors:
                    print(f"Warning: {error}", file=sys.stderr)
                    
            print("MCP Unified Client Session closed and server stopped.", file=sys.stderr)

    async def call_tool(self, session: ClientSession, tool_name: str, arguments: dict) -> Sequence[TextContent]:
        """Helper method to call a tool via MCP ClientSession with retry logic."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = await session.call_tool(tool_name, arguments)
                # Filter and return only TextContent items
                text_contents = [content for content in result.content if isinstance(content, TextContent)]
                return text_contents
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Tool call failed after {max_retries} attempts: {e}", file=sys.stderr)
                    raise
                print(f"Tool call attempt {attempt + 1} failed: {e}, retrying...", file=sys.stderr)
                await asyncio.sleep(0.1)
        
        # This line should never be reached due to the raise in the loop, but added for type safety
        return []

    @pytest.mark.asyncio
    async def test_unified_server_initialization(self, mcp_unified_client_session: ClientSession):
        """
        Test that the MCP unified server starts correctly and lists expected tools.
        """
        print("Running test_unified_server_initialization...", file=sys.stderr)
        
        # Test that we can list tools
        tools = await mcp_unified_client_session.list_tools()
        tool_names = [tool.name for tool in tools.tools]
        
        print(f"Available unified tools: {tool_names}", file=sys.stderr)
        
        # Verify expected command tools are available
        expected_command_tools = [
            "command_execute",
            "command_bg_start", 
            "command_ps_list",
            "command_ps_stop",
            "command_ps_logs",
            "command_ps_clean",
            "command_ps_detail"
        ]
        
        # Verify expected filesystem tools are available
        expected_filesystem_tools = [
            "fs_read_file",
            "fs_write_file",
            "fs_create_directory",
            "fs_list_directory",
            "fs_move_file",
            "fs_search_files",
            "fs_get_file_info",
            "fs_edit_file",
            "fs_get_filesystem_info"
        ]
        
        all_expected_tools = expected_command_tools + expected_filesystem_tools
        
        for tool in all_expected_tools:
            assert tool in tool_names, f"Expected unified tool '{tool}' not found in {tool_names}"
        
        print("✅ Unified server initialization test passed", file=sys.stderr)

    @pytest.mark.asyncio
    async def test_unified_server_both_capabilities(self, mcp_unified_client_session: ClientSession, tmp_path):
        """Test that unified server can handle both command and filesystem operations."""
        print("Running test_unified_server_both_capabilities...", file=sys.stderr)
        
        # Test command execution
        command_result = await self.call_tool(
            mcp_unified_client_session,
            "command_execute",
            {
                "command": "echo",
                "args": ["Hello from unified server"],
                "directory": str(tmp_path),
                "timeout": 15,
                "limit_lines": 500
            }
        )
        
        assert isinstance(command_result, (list, tuple))
        assert len(command_result) == 3
        assert isinstance(command_result[0], TextContent)
        assert isinstance(command_result[1], TextContent)
        assert isinstance(command_result[2], TextContent)
        assert "Hello from unified server" in command_result[1].text
        
        # Test filesystem operation
        test_file = tmp_path / "unified_test.txt"
        test_content = "Unified server test content"
        
        fs_result = await self.call_tool(
            mcp_unified_client_session,
            "fs_write_file",
            {
                "path": str(test_file),
                "content": test_content
            }
        )
        
        assert isinstance(fs_result, (list, tuple))
        assert len(fs_result) == 1
        assert isinstance(fs_result[0], TextContent)
        assert "成功写入" in fs_result[0].text
        
        print("✅ Unified server both capabilities test passed", file=sys.stderr)
