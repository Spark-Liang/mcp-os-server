"""
This module defines custom exceptions for the MCP Command Server.
"""
from typing import Optional

class CommandServerException(Exception):
    """Base class for all custom command server exceptions."""
    pass

class InitializationError(CommandServerException):
    """Raised when a component fails to initialize."""
    pass

class StorageError(CommandServerException):
    """Raised when an output storage operation fails."""
    pass

class OutputRetrievalError(CommandServerException):
    """Raised when retrieving process output fails."""
    pass

class OutputClearError(CommandServerException):
    """Raised when clearing process output fails."""
    pass

class ProcessNotFoundError(CommandServerException):
    """Raised when a specified process is not found."""
    pass

class CommandExecutionError(CommandServerException):
    """Raised when a command execution fails."""
    pass

class CommandTimeoutError(CommandServerException):
    """Raised when a command execution times out."""
    
    def __init__(self, message: str, pid: Optional[str] = None, stdout: Optional[str] = None, stderr: Optional[str] = None):
        super().__init__(message)
        self.pid = pid
        self.stdout = stdout or ""
        self.stderr = stderr or ""

class ProcessTimeoutError(CommandServerException):
    """Raised when a process execution times out."""
    pass

class ProcessControlError(CommandServerException):
    """Raised when a process control operation (e.g., stop, terminate) fails."""
    pass

class ProcessInfoRetrievalError(CommandServerException):
    """Raised when retrieving process information fails."""
    pass

class ProcessListRetrievalError(CommandServerException):
    """Raised when retrieving the process list fails."""
    pass

class ProcessCleanError(CommandServerException):
    """Raised when cleaning a process fails."""
    pass

class EnvironmentError(CommandServerException):
    """Raised for environment-related errors."""
    pass

class WebInterfaceError(CommandServerException):
    """Raised for web interface-related errors."""
    pass 