import asyncio
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from mcp_os_server.command.command_executor import CommandExecutor
from mcp_os_server.command.exceptions import (
    CommandExecutionError,
    CommandTimeoutError,
    ProcessNotFoundError,
)
from mcp_os_server.command.interfaces import IProcessManager, ProcessStatus
from mcp_os_server.command.output_manager import OutputManager
from mcp_os_server.command.process_manager_asyncio import AsyncioBaseProcessManager
from mcp_os_server.command.process_manager_subprocess import SubprocessBaseProcessManager

# Helper to find the command script
CMD_SCRIPT_PATH = (
    Path(__file__).parent.parent.parent.parent / "src" / "mcp_os_server" / "command" / "cmd_for_test.py"
).resolve()

if not CMD_SCRIPT_PATH.is_file():
    # Fallback for different execution environments
    CMD_SCRIPT_PATH = (
        Path(__file__).parent / "cmd_for_test.py"
    ).resolve()


@pytest_asyncio.fixture
async def output_manager(tmp_path: Path) -> AsyncGenerator[OutputManager, None]:
    """Provides a function-scoped OutputManager instance."""
    manager = OutputManager(output_storage_path=tmp_path.as_posix())
    yield manager
    await manager.shutdown()


class CommandExecutorTestBase(ABC):
    """Abstract base class for testing CommandExecutor with different ProcessManager implementations."""

    @pytest_asyncio.fixture
    @abstractmethod
    async def process_manager(self, output_manager: OutputManager) -> AsyncGenerator[IProcessManager, None]:
        """Provides a ProcessManager instance. Must be implemented by subclasses."""
        pass

    @pytest_asyncio.fixture
    async def executor(self, process_manager: IProcessManager) -> AsyncGenerator[CommandExecutor, None]:
        """Provides a CommandExecutor instance based on the process_manager fixture."""
        command_executor = CommandExecutor(process_manager=process_manager)
        await command_executor.initialize()
        yield command_executor
        # No shutdown needed here, as process_manager is handled separately

    @pytest.mark.asyncio
    async def test_execute_command_end_to_end(self, executor: CommandExecutor, tmp_path: Path):
        """端到端命令执行测试"""
        command = [sys.executable, str(CMD_SCRIPT_PATH), "echo", "hello", "world"]
        result = await executor.execute_command(command, directory=str(tmp_path))

        assert result.stdout.strip() == "hello world"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.execution_time > 0

    @pytest.mark.asyncio
    async def test_execute_command_timeout(self, executor: CommandExecutor, tmp_path: Path):
        """同步命令超时测试"""
        command = [sys.executable, str(CMD_SCRIPT_PATH), "sleep", "5"]
        with pytest.raises(CommandTimeoutError):
            await executor.execute_command(command, directory=str(tmp_path), timeout=1)

    @pytest.mark.asyncio
    async def test_execute_command_uninterruptible_timeout(self, executor: CommandExecutor, tmp_path: Path):
        """测试不可中断的死循环命令也能被超时控制"""
        command = [sys.executable, str(CMD_SCRIPT_PATH), "loop", "5"]
        with pytest.raises(CommandTimeoutError):
            await executor.execute_command(command, directory=str(tmp_path), timeout=1)

    @pytest.mark.asyncio
    async def test_execute_command_with_specific_exit_code(self, executor: CommandExecutor, tmp_path: Path):
        """测试命令返回特定退出码"""
        command = [sys.executable, str(CMD_SCRIPT_PATH), "exit", "42"]
        result = await executor.execute_command(command, directory=str(tmp_path))
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_execute_command_with_stdin(self, executor: CommandExecutor, tmp_path: Path):
        """测试带有stdin的命令执行"""
        command = [sys.executable, str(CMD_SCRIPT_PATH), "grep", "test"]
        stdin_data = b"line1\nline2 test\nline3"
        result = await executor.execute_command(
            command, directory=str(tmp_path), stdin_data=stdin_data
        )
        assert result.stdout.strip() == "line2 test"

    @pytest.mark.asyncio
    async def test_background_command_lifecycle(self, executor: CommandExecutor, tmp_path: Path):
        """后台任务生命周期测试"""
        # Use a command that produces output to ensure log files are created
        command = [sys.executable, str(CMD_SCRIPT_PATH), "sleep", "10"]
        process = await executor.start_background_command(
            command=command,
            directory=str(tmp_path),
            description="test sleep",
            labels=["test", "lifecycle"],
        )

        details = await executor.get_process_detail(process.pid)
        assert details.status == ProcessStatus.RUNNING
        assert details.command == command
        assert details.description == "test sleep"
        assert "test" in details.labels

        processes = await executor.list_process(status=ProcessStatus.RUNNING, labels=["test"])
        assert len(processes) >= 1
        assert any(p.pid == process.pid for p in processes)

        # Test logging
        await asyncio.sleep(1) # Give it a moment to run
        logs = [log.text.strip() async for log in executor.get_process_logs(process.pid, "stdout")]
        assert len(logs) == 0

        await executor.stop_process(process.pid)
        details_after_stop = await executor.get_process_detail(process.pid)
        assert details_after_stop.status == ProcessStatus.TERMINATED

        # Test cleanup
        result = await executor.clean_process([process.pid])
        assert result.get(process.pid) == "Success"

        # After cleaning, getting details should fail
        with pytest.raises(ProcessNotFoundError):
            await executor.get_process_detail(process.pid)

        # The process should also not appear in the list anymore
        all_processes = await executor.list_process()
        assert not any(p.pid == process.pid for p in all_processes)

    @pytest.mark.asyncio
    async def test_concurrent_background_commands(self, executor: CommandExecutor, tmp_path: Path):
        """并发后台命令测试"""
        commands = [
            [sys.executable, str(CMD_SCRIPT_PATH), "sleep", "5"] for _ in range(3)
        ]

        tasks = [
            executor.start_background_command(cmd, str(tmp_path), f"concurrent test {i}")
            for i, cmd in enumerate(commands)
        ]
        processes = await asyncio.gather(*tasks)
        pids = [p.pid for p in processes]

        running_processes = await executor.list_process(status=ProcessStatus.RUNNING)
        running_pids = {p.pid for p in running_processes}
        assert all(pid in running_pids for pid in pids)

        stop_tasks = [executor.stop_process(pid) for pid in pids]
        await asyncio.gather(*stop_tasks)

        for pid in pids:
            details = await executor.get_process_detail(pid)
            assert details.status == ProcessStatus.TERMINATED

    @pytest.mark.asyncio
    async def test_command_execution_error_for_nonexistent_command(self, executor: CommandExecutor, tmp_path: Path):
        """测试执行不存在的命令时抛出CommandExecutionError"""
        # This test relies on the underlying ProcessManager to raise CommandExecutionError
        # when the executable is not found.
        command = ["nonexistent-command-12345"]
        with pytest.raises(CommandExecutionError):
            await executor.execute_command(command, directory=str(tmp_path))

    @pytest.mark.asyncio
    async def test_cleanup_finished_process(self, executor: CommandExecutor, tmp_path: Path):
        """测试清理已完成的进程"""
        command = [sys.executable, str(CMD_SCRIPT_PATH), "echo", "done"]
        process = await executor.start_background_command(command, str(tmp_path), "to be cleaned")

        await process.wait_for_completion(timeout=5)

        details = await executor.get_process_detail(process.pid)
        assert details.status == ProcessStatus.COMPLETED
        assert details.exit_code == 0

        result = await executor.clean_process([process.pid])
        assert result.get(process.pid) == "Success"

        with pytest.raises(ProcessNotFoundError):
            await executor.get_process_detail(process.pid)

        # The process should also not appear in the list anymore
        all_processes = await executor.list_process()
        assert not any(p.pid == process.pid for p in all_processes)

    @pytest.mark.asyncio
    async def test_encoding_support(self, executor: CommandExecutor, tmp_path: Path):
        """环境变量与编码支持测试"""
        # Test gbk encoding
        text_gbk = "你好世界"
        command = [sys.executable, str(CMD_SCRIPT_PATH), "encode_echo", text_gbk]
        
        # On some systems, the default might not be utf-8, and direct print can fail.
        # We will test execution with gbk.
        try:
            result = await executor.execute_command(
                command, directory=str(tmp_path), encoding="gbk"
            )
            # 输出捕获已修复，中文字符应该能正确显示
            assert "Basic ASCII and Safe Chinese" in result.stdout
            assert text_gbk in result.stdout  # 修复后应该能获取到中文输出
        except (UnicodeEncodeError, LookupError):
            pytest.skip("System does not support gbk in console for testing")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("process_manager_class", [AsyncioBaseProcessManager, SubprocessBaseProcessManager])
    async def test_shutdown_stops_running_processes(self, tmp_path: Path, process_manager_class: type[IProcessManager]):
        """测试关闭时能停止所有正在运行的进程"""
        # We need a new set of components for this specific lifecycle test
        # to avoid interfering with other module-scoped fixtures.
        log_path = tmp_path / "shutdown_test_logs"
        log_path.mkdir()

        output_manager = OutputManager(output_storage_path=log_path.as_posix())
        # Use the parameterized class
        process_manager = process_manager_class(output_manager=output_manager, process_retention_seconds=5)
        await process_manager.initialize()
        executor = CommandExecutor(process_manager=process_manager)
        await executor.initialize()

        command = [sys.executable, str(CMD_SCRIPT_PATH), "sleep", "30"]
        process = await executor.start_background_command(command, str(tmp_path), "long running")

        # Verify it's running
        details = await executor.get_process_detail(process.pid)
        assert details.status == ProcessStatus.RUNNING

        # Shutdown the process manager. This should stop the process.
        await process_manager.shutdown()

        # The process object itself should reflect the change in status after its
        # wait task is cancelled or completed during shutdown.
        final_details = await process.get_details()
        assert final_details.status == ProcessStatus.TERMINATED

    @pytest.mark.asyncio
    async def test_command_pipe_simulation(self, executor: CommandExecutor, tmp_path: Path):
        """测试模拟管道功能 (cmd1 | cmd2)"""
        # 1. Execute the first command
        cmd1 = [sys.executable, str(CMD_SCRIPT_PATH), "echo", "line1\nline2\nline1 test"]
        result1 = await executor.execute_command(cmd1, directory=str(tmp_path))
        assert result1.exit_code == 0

        # 2. Use the stdout of the first command as stdin for the second command
        cmd2 = [sys.executable, str(CMD_SCRIPT_PATH), "grep", "test"]
        result2 = await executor.execute_command(
            cmd2,
            directory=str(tmp_path),
            stdin_data=result1.stdout.encode('utf-8')
        )
        assert result2.exit_code == 0
        assert result2.stdout.strip() == "line1 test"

    @pytest.mark.asyncio
    async def test_concurrent_stop_and_clean(self, executor: CommandExecutor, tmp_path: Path):
        """测试并发停止和清理进程"""
        # Start a few processes
        commands = [
            [sys.executable, str(CMD_SCRIPT_PATH), "sleep", "10"] for _ in range(3)
        ]
        processes = await asyncio.gather(
            *[executor.start_background_command(cmd, str(tmp_path), f"concurrent stop/clean test {i}") for i, cmd in enumerate(commands)]
        )
        pids = [p.pid for p in processes]

        # Concurrently stop and clean them
        # Note: In a real scenario, cleaning might fail if stop is not complete.
        # Here we test the atomicity and error handling of the server.
        stop_tasks = [executor.stop_process(pid) for pid in pids]
        await asyncio.gather(*stop_tasks)
        
        # Ensure they are all terminated
        for pid in pids:
            details = await executor.get_process_detail(pid)
            assert details.status == ProcessStatus.TERMINATED

        # Concurrently clean them
        clean_tasks = [executor.clean_process([pid]) for pid in pids]
        results = await asyncio.gather(*clean_tasks)

        # Check results
        assert all(res.get(pid) == "Success" for res, pid in zip(results, pids))
        
        # Verify they are all gone
        all_processes = await executor.list_process()
        assert not any(p.pid in pids for p in all_processes)

    @pytest.mark.asyncio
    async def test_path_lookup_functionality(self, executor: CommandExecutor, tmp_path: Path):
        """测试 PATH 查找功能 - 验证能够找到并执行在 PATH 中的脚本文件"""
        # 获取测试 CMD 文件的路径
        test_cmd_file = Path(__file__).parent / "test_cmd_file.cmd"
        
        # 确保测试文件存在
        assert test_cmd_file.exists(), f"Test CMD file not found: {test_cmd_file}"
        
        # 创建一个临时目录，并将测试 CMD 文件复制到其中
        test_bin_dir = tmp_path / "test_bin"
        test_bin_dir.mkdir()
        
        import shutil
        test_cmd_in_path = test_bin_dir / "test_cmd_file.cmd"
        shutil.copy2(test_cmd_file, test_cmd_in_path)
        
        # 设置环境变量，将临时目录添加到 PATH
        import os
        original_path = os.environ.get('PATH', '')
        test_envs = {
            'PATH': str(test_bin_dir) + os.pathsep + original_path
        }
        
        try:
            # 测试 1: 不带参数执行 - 应该能通过 PATH 查找找到并执行
            result1 = await executor.execute_command(
                command=["test_cmd_file.cmd"],
                directory=str(tmp_path),
                envs=test_envs
            )
            
            assert result1.exit_code == 0, f"Command failed with exit code {result1.exit_code}"
            # 验证输出包含预期信息（脚本可能输出多行或单行）
            output_lines = result1.stdout.strip()
            assert "Hello from test_cmd_file.cmd!" in output_lines or "Usage: test_cmd_file.cmd [message]" in output_lines
            
            # 测试 2: 带参数执行 - 验证参数传递正常
            result2 = await executor.execute_command(
                command=["test_cmd_file.cmd", "test", "message", "from", "PATH"],
                directory=str(tmp_path),
                envs=test_envs
            )
            
            assert result2.exit_code == 0, f"Command failed with exit code {result2.exit_code}"
            assert "Message from test_cmd_file.cmd: test message from PATH" in result2.stdout
            
            # 测试 3: 后台执行测试 - 验证后台进程也能正确使用 PATH 查找
            process = await executor.start_background_command(
                command=["test_cmd_file.cmd", "background", "execution", "test"],
                directory=str(tmp_path),
                description="PATH lookup background test",
                envs=test_envs
            )
            
            # 等待进程完成
            completed_info = await process.wait_for_completion(timeout=10)
            assert completed_info.status == ProcessStatus.COMPLETED
            assert completed_info.exit_code == 0
            
            # 获取输出验证
            logs = [log.text.strip() async for log in executor.get_process_logs(process.pid, "stdout")]
            output_text = '\n'.join(logs)
            assert "Message from test_cmd_file.cmd: background execution test" in output_text
            
            # 清理进程
            await executor.clean_process([process.pid])
            
        except Exception as e:
            if sys.platform != "win32":
                pytest.skip(f"This test is designed for Windows systems. Skipped on {sys.platform}: {e}")
            else:
                raise

    @pytest.mark.asyncio
    async def test_npm_path_lookup_optional(self, executor: CommandExecutor, tmp_path: Path):
        """可选测试：验证 npm PATH 查找功能 - 需要设置环境变量 TEST_NPM_ENABLED=1 启用"""
        import os
        
        # 检查是否启用了 npm 测试
        if not os.environ.get('TEST_NPM_ENABLED', '').lower() in ('1', 'true', 'yes'):
            pytest.skip("NPM test is disabled. Set TEST_NPM_ENABLED=1 to enable this test.")
        
        try:
            # 测试 1: 检查 npm 版本 - 验证能通过 PATH 找到 npm
            result1 = await executor.execute_command(
                command=["npm", "--version"],
                directory=str(tmp_path),
                timeout=30  # npm 可能需要较长时间启动
            )
            
            assert result1.exit_code == 0, f"npm --version failed with exit code {result1.exit_code}. stderr: {result1.stderr}"
            version_output = result1.stdout.strip()
            assert len(version_output) > 0, "npm version output is empty"
            # 验证版本号格式（通常是 x.y.z 格式）
            import re
            version_pattern = r'\d+\.\d+\.\d+'
            assert re.search(version_pattern, version_output), f"Invalid npm version format: {version_output}"
            
            print(f"[OK] NPM version: {version_output}")
            
            # 测试 2: npm help 命令 - 验证 npm 基本功能
            result2 = await executor.execute_command(
                command=["npm", "help"],
                directory=str(tmp_path),
                timeout=30
            )
            
            assert result2.exit_code == 0, f"npm help failed with exit code {result2.exit_code}. stderr: {result2.stderr}"
            help_output = result2.stdout.strip()
            # npm help 可能输出版本信息、路径信息等，只要包含 npm 字符串并且成功执行就说明工作正常
            assert "npm" in help_output.lower(), f"Unexpected npm help output: {help_output[:200]}..."
            
            print("[OK] NPM help command works correctly")
            
            # 测试 3: 后台执行 npm 命令
            process = await executor.start_background_command(
                command=["npm", "--version"],
                directory=str(tmp_path),
                description="NPM PATH lookup background test",
                timeout=30
            )
            
            # 等待进程完成
            completed_info = await process.wait_for_completion(timeout=45)
            assert completed_info.status == ProcessStatus.COMPLETED, f"NPM background process failed with status: {completed_info.status}"
            assert completed_info.exit_code == 0, f"NPM background process failed with exit code: {completed_info.exit_code}"
            
            # 获取输出验证
            logs = [log.text.strip() async for log in executor.get_process_logs(process.pid, "stdout")]
            output_text = '\n'.join(logs)
            assert re.search(version_pattern, output_text), f"Invalid npm version in background output: {output_text}"
            
            print("[OK] NPM background execution works correctly")
            
            # 清理进程
            await executor.clean_process([process.pid])
            
            print("[SUCCESS] All NPM PATH lookup tests passed successfully!")
            
        except CommandExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip(f"npm not found in PATH. Please install Node.js/npm to run this test. Error: {e}")
            else:
                raise
        except Exception as e:
            pytest.fail(f"NPM test failed with unexpected error: {e}")

    @pytest.mark.asyncio
    async def test_cmd_script_execution(self, executor: CommandExecutor, tmp_path: Path):
        """测试 .CMD 脚本文件执行功能"""
        # 获取测试 CMD 文件的路径
        test_cmd_file = Path(__file__).parent / "test_cmd_file.cmd"
        
        # 确保测试文件存在
        assert test_cmd_file.exists(), f"Test CMD file not found: {test_cmd_file}"
        
        try:
            # 测试 1: 直接执行 .cmd 文件 (不使用 PATH 查找)
            result1 = await executor.execute_command(
                command=[str(test_cmd_file)],
                directory=str(tmp_path),
                timeout=10
            )
            
            assert result1.exit_code == 0, f"CMD execution failed with exit code {result1.exit_code}"
            output_lines = result1.stdout.strip()
            assert "Hello from test_cmd_file.cmd!" in output_lines or "Usage: test_cmd_file.cmd [message]" in output_lines
            
            # 测试 2: 带参数执行 .cmd 文件
            result2 = await executor.execute_command(
                command=[str(test_cmd_file), "direct", "execution", "test"],
                directory=str(tmp_path),
                timeout=10
            )
            
            assert result2.exit_code == 0, f"CMD with args execution failed with exit code {result2.exit_code}"
            assert "Message from test_cmd_file.cmd: direct execution test" in result2.stdout
            
            print("[OK] Direct .CMD script execution works correctly")
            
        except Exception as e:
            if sys.platform != "win32":
                pytest.skip(f"This test is designed for Windows systems. Skipped on {sys.platform}: {e}")
            else:
                raise

    @pytest.mark.asyncio
    async def test_npm_debug_execution(self, executor: CommandExecutor, tmp_path: Path):
        """调试测试：专门检查 npm 执行情况"""
        import os
        
        # 检查是否启用了 npm 测试
        if not os.environ.get('TEST_NPM_ENABLED', '').lower() in ('1', 'true', 'yes'):
            pytest.skip("NPM debug test is disabled. Set TEST_NPM_ENABLED=1 to enable this test.")
        
        try:
            # 第一步：检查 npm 路径
            import shutil
            npm_path = shutil.which('npm')
            print(f"npm found at: {npm_path}")
            
            if not npm_path:
                pytest.skip("npm not found in PATH")
                
            print(f"npm path extension: {npm_path.lower().split('.')[-1] if '.' in npm_path else 'no extension'}")
            
            # 第二步：尝试最简单的 npm 命令
            try:
                result = await executor.execute_command(
                    command=["npm"],  # 只运行 npm，不带参数
                    directory=str(tmp_path),
                    timeout=15
                )
                print(f"npm (no args) exit code: {result.exit_code}")
                print(f"npm (no args) stdout: {result.stdout[:200]}...")
                print(f"npm (no args) stderr: {result.stderr[:200]}...")
                
            except Exception as e:
                print(f"npm (no args) failed: {e}")
            
            # 第三步：尝试 npm --version
            try:
                result = await executor.execute_command(
                    command=["npm", "--version"],
                    directory=str(tmp_path),
                    timeout=15
                )
                print(f"npm --version exit code: {result.exit_code}")
                print(f"npm --version stdout: {result.stdout}")
                print(f"npm --version stderr: {result.stderr}")
                
                if result.exit_code == 0:
                    print("[SUCCESS] npm --version works through direct execution")
                else:
                    print(f"[FAIL] npm --version failed with exit code {result.exit_code}")
                    
            except Exception as e:
                print(f"npm --version failed: {e}")
                raise
                
        except Exception as e:
            print(f"npm debug test failed: {e}")
            raise

    @pytest.mark.asyncio
    async def test_mcp_command_simulation(self, executor: CommandExecutor, tmp_path: Path):
        """模拟 MCP command_execute 工具的流程，但不通过完整的 MCP 协议"""
        import os
        
        # 检查是否启用了 npm 测试
        if not os.environ.get('TEST_NPM_ENABLED', '').lower() in ('1', 'true', 'yes'):
            pytest.skip("NPM simulation test is disabled. Set TEST_NPM_ENABLED=1 to enable this test.")
        
        try:
            # 模拟 MCP command_execute 工具的参数
            command = "npm"
            args = ["--version"]
            directory = str(tmp_path)
            timeout = 30
            envs = {"PATH": os.environ.get('PATH', '')} if os.environ.get('PATH') else None
            
            # 检查命令是否在允许的命令列表中（模拟 MCP 服务器的验证）
            allowed_commands = ["npm", "echo", "node", "python"]  # 模拟允许的命令
            if command not in allowed_commands:
                pytest.fail(f"Command '{command}' is not allowed. Allowed commands: {', '.join(allowed_commands)}")
            
            # 执行命令（这与 MCP command_execute 工具中的执行逻辑相同）
            result = await executor.execute_command(
                command=[command] + (args or []),
                directory=directory,
                stdin_data=None,
                timeout=timeout,
                envs=envs,
                encoding=None,
            )
            
            # 验证结果（模拟 MCP 工具的响应格式化）
            if result.exit_code == 0:
                response_text = result.stdout
                print(f"[OK] MCP simulation - npm version: {response_text.strip()}")
            else:
                response_text = f"Command failed with exit code {result.exit_code}:\n{result.stderr}"
                pytest.fail(f"Command execution failed: {response_text}")
                
            # 验证版本号格式
            import re
            version_pattern = r'\d+\.\d+\.\d+'
            assert re.search(version_pattern, response_text), f"Invalid npm version format: {response_text}"
            
            print("[SUCCESS] MCP command simulation test passed")
            
        except Exception as e:
            print(f"MCP command simulation test failed: {e}")
            raise

    @pytest.mark.asyncio
    async def test_complete_output_capture_on_normal_exit(self, executor: CommandExecutor, tmp_path: Path):
        """测试正常退出的进程能获取完整的stdout和stderr输出"""
        # 使用multi_output命令产生大量输出
        command = [sys.executable, str(CMD_SCRIPT_PATH), "multi_output", "20", "--stderr"]
        result = await executor.execute_command(command, directory=str(tmp_path))

        # 验证退出码为0
        assert result.exit_code == 0

        # 验证stdout输出完整性
        stdout_lines = result.stdout.strip().split('\n')
        assert len(stdout_lines) == 20, f"Expected 20 stdout lines, got {len(stdout_lines)}"
        
        for i in range(20):
            expected_line = f"stdout line {i+1}: This is output line number {i+1}"
            assert expected_line in stdout_lines[i], f"Missing expected stdout line: {expected_line}"

        # 验证stderr输出完整性
        stderr_lines = result.stderr.strip().split('\n')
        assert len(stderr_lines) == 20, f"Expected 20 stderr lines, got {len(stderr_lines)}"
        
        for i in range(20):
            expected_line = f"stderr line {i+1}: This is error line number {i+1}"
            assert expected_line in stderr_lines[i], f"Missing expected stderr line: {expected_line}"

    @pytest.mark.asyncio
    async def test_complete_output_capture_on_abnormal_exit(self, executor: CommandExecutor, tmp_path: Path):
        """测试异常退出的进程能获取完整的stdout和stderr输出"""
        # 使用fail_with_output命令产生输出后异常退出
        command = [sys.executable, str(CMD_SCRIPT_PATH), "fail_with_output", "5", "42"]
        result = await executor.execute_command(command, directory=str(tmp_path))

        # 验证退出码为42
        assert result.exit_code == 42

        # 验证stdout输出完整性
        stdout_lines = result.stdout.strip().split('\n')
        # 应该有5行输出 + 1行 "Process is about to fail"
        assert len(stdout_lines) == 6, f"Expected 6 stdout lines, got {len(stdout_lines)}"
        
        for i in range(5):
            expected_line = f"Output before failure line {i+1}"
            assert expected_line in stdout_lines[i], f"Missing expected stdout line: {expected_line}"
        
        assert "Process is about to fail" in stdout_lines[5]

        # 验证stderr输出完整性
        stderr_lines = result.stderr.strip().split('\n')
        # 应该有5行错误输出 + 1行 "Final error message"
        assert len(stderr_lines) == 6, f"Expected 6 stderr lines, got {len(stderr_lines)}"
        
        for i in range(5):
            expected_line = f"Error before failure line {i+1}"
            assert expected_line in stderr_lines[i], f"Missing expected stderr line: {expected_line}"
        
        assert "Final error message" in stderr_lines[5]

    @pytest.mark.asyncio
    async def test_background_process_complete_output_capture(self, executor: CommandExecutor, tmp_path: Path):
        """测试后台进程正常和异常退出后都能获取完整输出"""
        
        # 测试1: 正常退出的后台进程
        command1 = [sys.executable, str(CMD_SCRIPT_PATH), "multi_output", "10", "--stderr"]
        process1 = await executor.start_background_command(
            command=command1,
            directory=str(tmp_path),
            description="Background multi output test",
            labels=["output_test"]
        )

        # 等待进程完成
        completed_info1 = await process1.wait_for_completion(timeout=30)
        assert completed_info1.status == ProcessStatus.COMPLETED
        assert completed_info1.exit_code == 0

        # 获取完整的stdout输出
        stdout_logs = [log.text async for log in executor.get_process_logs(process1.pid, "stdout")]
        assert len(stdout_logs) == 10, f"Expected 10 stdout logs, got {len(stdout_logs)}"
        
        for i in range(10):
            expected_text = f"stdout line {i+1}: This is output line number {i+1}"
            assert expected_text in stdout_logs[i], f"Missing expected stdout: {expected_text}"

        # 获取完整的stderr输出
        stderr_logs = [log.text async for log in executor.get_process_logs(process1.pid, "stderr")]
        assert len(stderr_logs) == 10, f"Expected 10 stderr logs, got {len(stderr_logs)}"
        
        for i in range(10):
            expected_text = f"stderr line {i+1}: This is error line number {i+1}"
            assert expected_text in stderr_logs[i], f"Missing expected stderr: {expected_text}"

        # 清理进程1
        await executor.clean_process([process1.pid])

        # 测试2: 异常退出的后台进程
        command2 = [sys.executable, str(CMD_SCRIPT_PATH), "fail_with_output", "3", "99"]
        process2 = await executor.start_background_command(
            command=command2,
            directory=str(tmp_path),
            description="Background fail with output test",
            labels=["output_test"]
        )

        # 等待进程完成
        completed_info2 = await process2.wait_for_completion(timeout=30)
        assert completed_info2.status == ProcessStatus.FAILED
        assert completed_info2.exit_code == 99

        # 获取完整的stdout输出
        stdout_logs2 = [log.text async for log in executor.get_process_logs(process2.pid, "stdout")]
        # 应该有3行 + 1行 "Process is about to fail"
        assert len(stdout_logs2) == 4, f"Expected 4 stdout logs, got {len(stdout_logs2)}"

        # 获取完整的stderr输出
        stderr_logs2 = [log.text async for log in executor.get_process_logs(process2.pid, "stderr")]
        # 应该有3行 + 1行 "Final error message"
        assert len(stderr_logs2) == 4, f"Expected 4 stderr logs, got {len(stderr_logs2)}"

        # 清理进程2
        await executor.clean_process([process2.pid])

    @pytest.mark.asyncio
    async def test_large_output_capture(self, executor: CommandExecutor, tmp_path: Path):
        """测试大量输出的完整捕获"""
        # 使用更大的输出量测试
        command = [sys.executable, str(CMD_SCRIPT_PATH), "multi_output", "100"]
        result = await executor.execute_command(command, directory=str(tmp_path))

        assert result.exit_code == 0

        # 验证所有100行都被捕获
        stdout_lines = result.stdout.strip().split('\n')
        assert len(stdout_lines) == 100, f"Expected 100 stdout lines, got {len(stdout_lines)}"
        
        # 验证第一行和最后一行
        assert "stdout line 1: This is output line number 1" in stdout_lines[0]
        assert "stdout line 100: This is output line number 100" in stdout_lines[99]


class TestAsyncCommandExecutor(CommandExecutorTestBase):
    """Test CommandExecutor with the standard asyncio-based ProcessManager."""
    @pytest_asyncio.fixture
    async def process_manager(self, output_manager: OutputManager) -> AsyncGenerator[IProcessManager, None]:
        """Provides a function-scoped ProcessManager instance."""
        manager = AsyncioBaseProcessManager(output_manager=output_manager, process_retention_seconds=5)
        await manager.initialize()
        yield manager
        await manager.shutdown()


class TestSubprocessCommandExecutor(CommandExecutorTestBase):
    """Test CommandExecutor with the subprocess-based ProcessManager."""
    @pytest_asyncio.fixture
    async def process_manager(self, output_manager: OutputManager) -> AsyncGenerator[IProcessManager, None]:
        """Provides a function-scoped SubprocessBaseProcessManager instance."""
        manager = SubprocessBaseProcessManager(output_manager=output_manager, process_retention_seconds=5)
        await manager.initialize()
        yield manager
        await manager.shutdown()
