import os
import sys
import pytest
import shutil # For shutil.which
import subprocess # For subprocess.PIPE
from typing import List

from anyio import run_process # Removed create_task_group as it's not needed for reading streams after run_process

pytestmark = pytest.mark.asyncio # Mark all tests in this module as asyncio tests, consistent with test_main_integration.py

# Removed _read_stream_and_append as run_process directly captures output

@pytest.mark.asyncio # Explicitly mark this function as an asyncio test
async def test_command_execute_uv_anyio_optional(tmp_path):
    """
    Optional integration test: validate running `uv run python -c print()` via anyio.create_process.
    Requires TEST_UV_ENABLED=1 environment variable to be set.
    """
    if not os.environ.get('TEST_UV_ENABLED', '').lower() in ('1', 'true', 'yes'):
        pytest.skip("UV integration test is disabled. Set TEST_UV_ENABLED=1 to enable this test.")
    
    if not shutil.which("uv"):
        pytest.skip("uv is not installed or not in PATH.")

    msg = "你好！"
    python_code = f"print('{msg}')"
    command = ["uv", "run", "python", "-c", python_code]
    encoding = "utf-8"

    # run_process captures stdout/stderr directly upon completion
    process = await run_process(
        command,
        cwd=str(tmp_path),
        stdin=None, # Use None for default behavior, or provide bytes for stdin
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Directly access captured stdout and stderr (which are bytes)
    stdout_output_bytes = process.stdout
    stderr_output_bytes = process.stderr

    # Decode and split into lines for assertion
    stdout_lines = [line.rstrip('\r\n').strip() for line in stdout_output_bytes.decode(encoding, errors='replace').splitlines()]
    stderr_lines = [line.rstrip('\r\n').strip() for line in stderr_output_bytes.decode(encoding, errors='replace').splitlines()]

    return_code = process.returncode

    assert return_code == 0, f"uv command exited with non-zero code: {return_code}, Stderr: {stderr_lines}"
    assert stderr_lines == [], f"Unexpected stderr output from uv: {stderr_lines}"
    assert msg in stdout_lines, f"Expected '{msg}' in stdout: {stdout_lines}"

@pytest.mark.asyncio # Explicitly mark this function as an asyncio test
async def test_command_execute_node_anyio_optional(tmp_path):
    """
    Optional integration test: validate running `node -e "console.log(...)"` via anyio.create_process.
    Requires TEST_NODE_ENABLED=1 environment variable to be set.
    """
    if not os.environ.get('TEST_NODE_ENABLED', '').lower() in ('1', 'true', 'yes'):
        pytest.skip("Node.js integration test is disabled. Set TEST_NODE_ENABLED=1 to enable this test.")
    
    if not shutil.which("node"):
        pytest.skip("Node.js is not installed or not in PATH.")

    msg = "你好！"
    # Using single quotes for the outer shell, and double for JSON inside console.log is safer
    node_code = f"console.log('{msg}')"
    command = ["node", "-e", node_code]
    encoding = "utf-8"

    # run_process captures stdout/stderr directly upon completion
    process = await run_process(
        command,
        cwd=str(tmp_path),
        stdin=None, # Use None for default behavior, or provide bytes for stdin
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Directly access captured stdout and stderr (which are bytes)
    stdout_output_bytes = process.stdout
    stderr_output_bytes = process.stderr

    # Decode and split into lines for assertion
    stdout_lines = [line.rstrip('\r\n').strip() for line in stdout_output_bytes.decode(encoding, errors='replace').splitlines()]
    stderr_lines = [line.rstrip('\r\n').strip() for line in stderr_output_bytes.decode(encoding, errors='replace').splitlines()]

    return_code = process.returncode

    assert return_code == 0, f"Node.js command exited with non-zero code: {return_code}, Stderr: {stderr_lines}"
    assert stderr_lines == [], f"Unexpected stderr output from Node.js: {stderr_lines}"
    assert msg in stdout_lines, f"Expected '{msg}' in stdout: {stdout_lines}"

