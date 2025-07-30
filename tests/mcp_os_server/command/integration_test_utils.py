import shutil
from pathlib import Path

CMD_SCRIPT_PATH = Path(__file__).parent / "cmd_for_test.py"
"""
Path to the helper script for testing.

This script is used to test the command execution functionality.
It is a simple script that prints the command and its arguments.
"""


def command_exists(command: str) -> bool:
    """
    Check if a command is available in the system's PATH.

    Args:
        command: The command to check.

    Returns:
        True if the command is available in the system's PATH, False otherwise.
    """
    return shutil.which(command) is not None
