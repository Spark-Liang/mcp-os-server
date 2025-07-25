import asyncio
import os
import sys
import pytest
from typing import Optional, List, Dict
import shutil # Import shutil for shutil.which

from mcp_os_server.command.exceptions import CommandExecutionError
from mcp_os_server.command.process_manager_subprocess import create_process

# Path to the helper script
# CMD_SCRIPT_PATH = os.path.join("tests", "mcp_os_server", "command", "print_args_and_stdin.py")
# Moved to fixture to ensure it's in the tmp_path

# Mark all tests in this module as asyncio
pytestmark = pytest.mark.asyncio

@pytest.fixture
def tmp_test_dir(tmp_path):
    """Fixture to provide a temporary directory for tests, including the helper script."""
    script_original_path = os.path.join(os.path.dirname(__file__), "print_args_and_stdin.py")
    script_dest_path = tmp_path / "print_args_and_stdin.py"
    shutil.copyfile(script_original_path, script_dest_path)
    return str(tmp_path)

async def _read_stream(stream, encoding: str) -> List[str]:
    """Helper to read all lines from an asyncio stream until EOF."""
    lines = []
    while True:
        line_bytes = await asyncio.to_thread(stream.readline)
        if not line_bytes:
            break
        # Use rstrip('\\r\\n') to remove platform-specific newlines, then strip() to remove leading/trailing whitespace including any quotes.
        lines.append(line_bytes.decode(encoding, errors='replace').rstrip('\\r\\n').strip())
    return lines

async def test_create_process_echo_success(tmp_test_dir: str):
    """Test create_process with a simple echo command and verify stdout."""
    command = ["echo", "Hello, World!"]
    encoding = "utf-8" # Use UTF-8 for consistency
    
    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )
    
    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))
    
    # Wait for the process to complete
    await asyncio.to_thread(process.wait)
    
    stdout_lines = await stdout_task
    stderr_lines = await stderr_task
    
    assert process.returncode == 0
    
    # On Windows, 'echo Hello, World!' might output 'Hello, World!' followed by an empty line
    # or just 'Hello, World!' depending on cmd.exe version/context.
    # We need to be flexible. Also, cmd.exe might add quotes.
    assert "Hello, World!" in stdout_lines
    assert stderr_lines == []

async def test_create_process_stderr_output(tmp_test_dir: str):
    """Test create_process with a command that writes to stderr."""
    # This command uses Python to print to stderr
    command = [sys.executable, "-c", "import sys; sys.stderr.write('Error message\\n')"]
    encoding = "utf-8"
    
    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )
    
    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))
    
    await asyncio.to_thread(process.wait)
    
    stdout_lines = await stdout_task
    stderr_lines = await stderr_task
    
    assert process.returncode == 0
    assert stdout_lines == []
    assert stderr_lines == ["Error message"]

async def test_create_process_stdin_input(tmp_test_dir: str):
    """Test create_process with stdin input."""
    # This command uses Python to read from stdin and print to stdout
    command = [sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"]
    test_input = "This is a test input."
    encoding = "utf-8"
    
    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=test_input.encode(encoding), # Provide stdin as bytes
    )
    
    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))
    
    await asyncio.to_thread(process.wait)
    
    stdout_lines = await stdout_task
    stderr_lines = await stderr_task
    
    assert process.returncode == 0
    assert stdout_lines == [test_input]
    assert stderr_lines == []

async def test_create_process_command_not_found(tmp_test_dir: str):
    """Test create_process when the command is not found."""
    command = ["nonexistent_command_xyz123"]
    encoding = "utf-8"
    
    with pytest.raises(CommandExecutionError) as excinfo:
        await create_process(
            command=command,
            directory=tmp_test_dir,
            encoding=encoding,
            envs={},
            stdin_data=None,
        )
    assert "Command not found" in str(excinfo.value)
    
async def test_create_process_invalid_directory(tmp_test_dir: str):
    """Test create_process with an invalid directory."""
    command = ["echo", "hello"]
    invalid_dir = os.path.join(tmp_test_dir, "nonexistent_dir_abc123")
    encoding = "utf-8"
    
    with pytest.raises(CommandExecutionError) as excinfo:
        await create_process(
            command=command,
            directory=invalid_dir,
            encoding=encoding,
            envs={},
            stdin_data=None,
        )
    assert "Directory not found" in str(excinfo.value)


async def test_create_process_with_unicode_args(tmp_test_dir: str):
    """
    Test create_process with Unicode arguments, verifying correct handling
    of non-ASCII characters in command line arguments.
    """
    unicode_arg = "你好世界，这是一个测试参数"
    script_path = os.path.join(tmp_test_dir, "print_args_and_stdin.py") # Use path from fixture
    command = [sys.executable, script_path, unicode_arg]
    encoding = "utf-8" # Use UTF-8 for consistency with Python internal string handling

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0, f"Process exited with non-zero code: {process.returncode}, Stderr: {stderr_lines}"
    assert stderr_lines == [], f"Unexpected stderr output: {stderr_lines}"

    # On Windows, sys.argv might contain the executable path itself, or sometimes paths are mangled.
    # We are looking for the specific unicode argument to be present in the output.
    expected_output_fragment = f"Args: ['{unicode_arg}']"
    assert any(expected_output_fragment in line for line in stdout_lines), \
        f"Expected fragment '{expected_output_fragment}' not found in stdout: {stdout_lines}"


async def test_create_process_with_node_command(tmp_test_dir: str):
    """Test create_process with 'node -e console.log()' command."""
    if not shutil.which("node"):
        pytest.skip("Node.js is not installed or not in PATH.")

    msg="Node.js 测试输出"
    node_code = f"console.log('{msg}')"
    command = ["node", "-e", node_code]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0, f"Node.js command exited with non-zero code: {process.returncode}, Stderr: {stderr_lines}"
    assert stderr_lines == [], f"Unexpected stderr output from Node.js: {stderr_lines}"
    assert msg in stdout_lines, f"Expected '{msg}' in stdout: {stdout_lines}"


async def test_create_process_with_uv_command(tmp_test_dir: str):
    """Test create_process with 'uv run python -c print()' command."""
    if not shutil.which("uv"):
        pytest.skip("uv is not installed or not in PATH.")

    msg="uv 测试输出"
    python_code = f"print('{msg}')"
    command = ["uv", "run", "python", "-c", python_code]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0, f"uv command exited with non-zero code: {process.returncode}, Stderr: {stderr_lines}"
    # uv might output some info to stderr, so we don't assert it's empty
    assert msg in stdout_lines, f"Expected '{msg}' in stdout: {stdout_lines}"


async def test_create_process_with_npm_command(tmp_test_dir: str):
    """Test create_process with 'npm --version' command."""
    if not shutil.which("npm"):
        pytest.skip("npm is not installed or not in PATH.")

    command = ["npm", "--version"]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0, f"npm command exited with non-zero code: {process.returncode}, Stderr: {stderr_lines}"
    assert stderr_lines == [], f"Unexpected stderr output from npm: {stderr_lines}"
    # npm --version usually outputs just the version number, so expect one non-empty line
    assert len(stdout_lines) >= 1 and len(stdout_lines[0].strip()) > 0, f"npm --version output not found or empty: {stdout_lines}"
    import re
    assert re.search(r'\d+\.\d+\.\d+.*' , stdout_lines[0].strip()), f"npm version format invalid: {stdout_lines[0]}"


async def test_create_process_with_mvn_command(tmp_test_dir: str):
    """Test create_process with 'mvn --version' command."""
    if not shutil.which("mvn"):
        pytest.skip("mvn is not installed or not in PATH.")

    command = ["mvn", "--version"]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0, f"mvn command exited with non-zero code: {process.returncode}, Stderr: {stderr_lines}"
    assert stderr_lines == [], f"Unexpected stderr output from mvn: {stderr_lines}"
    assert any("Apache Maven" in line for line in stdout_lines), f"Expected 'Apache Maven' in stdout: {stdout_lines}"


async def test_create_process_with_args_containing_spaces(tmp_test_dir: str):
    """Test create_process with arguments containing spaces to ensure proper quoting/passing."""
    # Use the helper script that prints arguments as they are received
    arg_with_spaces = "argument with spaces"
    script_path = os.path.join(tmp_test_dir, "print_args_and_stdin.py") # Use path from fixture
    command = [sys.executable, script_path, arg_with_spaces]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0
    assert stderr_lines == []
    # The print_args_and_stdin.py script prints the repr of the arguments list
    assert f"Args: {repr([arg_with_spaces])}" in stdout_lines, f"Expected argument with spaces to be passed as single arg: {stdout_lines}"


async def test_create_process_with_args_containing_newlines(tmp_test_dir: str):
    """Test create_process with arguments containing newlines."""
    arg_with_newlines = "line1\nline2\r\nline3"
    script_path = os.path.join(tmp_test_dir, "print_args_and_stdin.py") # Use path from fixture
    command = [sys.executable, script_path, arg_with_newlines]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0
    assert stderr_lines == []
    # The print_args_and_stdin.py script prints the repr of the arguments list
    assert f"Args: {repr([arg_with_newlines])}" in stdout_lines, f"Expected argument with newlines to be passed correctly: {stdout_lines}"


async def test_create_process_with_args_containing_shell_special_chars(tmp_test_dir: str):
    """Test create_process with arguments containing shell special characters."""
    # These characters should be treated as literal parts of the argument, not shell commands.
    special_arg = "; ls -la > output.txt && echo DONE"
    script_path = os.path.join(tmp_test_dir, "print_args_and_stdin.py") # Use path from fixture
    command = [sys.executable, script_path, special_arg]
    encoding = "utf-8"

    process = await create_process(
        command=command,
        directory=tmp_test_dir,
        encoding=encoding,
        envs={},
        stdin_data=None,
    )

    stdout_task = asyncio.create_task(_read_stream(process.stdout, encoding))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, encoding))

    await asyncio.to_thread(process.wait)

    stdout_lines = await stdout_task
    stderr_lines = await stderr_task

    assert process.returncode == 0
    assert stderr_lines == []
    # The print_args_and_stdin.py script prints the repr of the arguments list
    assert f"Args: {repr([special_arg])}" in stdout_lines, f"Expected special characters to be literal in argument: {stdout_lines}"
    # Ensure no shell injection occurred (e.g., no 'output.txt' created or 'DONE' echoed separately)
    assert not any("output.txt" in line or "DONE" in line for line in stdout_lines if line != f"Args: {repr([special_arg])}"), \
        "Shell injection detected! Special characters were interpreted."
